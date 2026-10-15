import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import shutil
import time
import zipfile
from datetime import datetime
from functools import wraps
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, g, jsonify, redirect, render_template, request, send_file, session, url_for

import app as face_app


web_app = Flask(__name__)
web_app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
web_app.secret_key = os.environ.get("FACE_APP_SECRET_KEY") or secrets.token_hex(32)
web_app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true",
)

ROOT = Path(__file__).resolve().parent
APP_DATA_DIR = Path(os.environ.get("APP_DATA_DIR", str(ROOT))).expanduser().resolve()
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_PATH = APP_DATA_DIR / "recognition_history.csv"
PEOPLE_META_PATH = APP_DATA_DIR / "people_metadata.json"
AUTH_DB_PATH = APP_DATA_DIR / "project_users.sqlite3"
HISTORY_FIELDS = ["time", "event", "predicted", "expected", "distance", "correct"]
MIN_IMAGES_PER_PERSON = 3
IMAGE_TARGET = 4
PERSON_TYPES = {"Student", "Employee", "Visitor", "Security staff"}


def connect_db():
    connection = sqlite3.connect(AUTH_DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_db():
    with connect_db() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'operator')),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS signup_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                category TEXT NOT NULL CHECK(category IN ('Student', 'Employee')),
                institution_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS member_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                participant_id TEXT NOT NULL UNIQUE,
                institution_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant TEXT NOT NULL,
                event TEXT NOT NULL CHECK(event IN ('check_in', 'check_out')),
                event_time TEXT NOT NULL,
                operator TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS access_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('granted', 'denied')),
                reason TEXT NOT NULL,
                distance REAL,
                event_time TEXT NOT NULL,
                operator TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS visitor_passes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_id TEXT NOT NULL,
                host_username TEXT NOT NULL COLLATE NOCASE,
                purpose TEXT NOT NULL,
                starts_at TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'rejected')),
                pass_code TEXT,
                requested_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                decided_by TEXT,
                decided_at TEXT
            );
        """)


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310000)
    return f"{salt.hex()}:{digest.hex()}"


def password_matches(password, stored):
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        computed = hash_password(password, bytes.fromhex(salt_hex)).split(":", 1)[1]
        return hmac.compare_digest(computed, digest_hex)
    except (ValueError, TypeError):
        return False


def user_count():
    with connect_db() as connection:
        return connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Please sign in to continue."}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Please sign in to continue."}), 401
        if session.get("role") != "admin":
            return jsonify({"error": "Admin access is required for this action."}), 403
        return view(*args, **kwargs)
    return wrapped


def staff_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Please sign in to continue."}), 401
        if session.get("role") not in {"admin", "operator"}:
            return jsonify({"error": "This page is only available to project operators."}), 403
        return view(*args, **kwargs)
    return wrapped


def member_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Please sign in to continue."}), 401
        if session.get("role") != "member" or not session.get("participant_id"):
            return jsonify({"error": "A student or employee account is required."}), 403
        return view(*args, **kwargs)
    return wrapped


initialize_db()


def read_people_metadata():
    if not PEOPLE_META_PATH.exists():
        return {}
    try:
        return json.loads(PEOPLE_META_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_people_metadata(metadata):
    PEOPLE_META_PATH.write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )


def ensure_display_labels(folders, metadata):
    """Use each participant's entered name as the visible label."""
    changed = False
    for folder in sorted(folders):
        info = metadata.setdefault(folder.name, {})
        category = info.get("category") or "Student"
        info["category"] = category
        display_name = info.get("display_name") or folder.name.replace("_", " ")
        if info.get("display_name") != display_name:
            info["display_name"] = display_name
            changed = True
        if info.get("display_label") != display_name:
            info["display_label"] = display_name
            changed = True
        if "access_authorized" not in info:
            info["access_authorized"] = False
            changed = True
    if changed:
        save_people_metadata(metadata)
    return metadata


def read_uploaded_image():
    uploaded = request.files.get("image")
    if uploaded is None:
        return None

    data = np.frombuffer(uploaded.read(), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def read_history_rows():
    if not HISTORY_PATH.exists():
        return []

    with HISTORY_PATH.open("r", newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def add_history(event, predicted="", expected="", distance="", correct=""):
    file_exists = HISTORY_PATH.exists()

    with HISTORY_PATH.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=HISTORY_FIELDS)
        if not file_exists:
            writer.writeheader()

        writer.writerow({
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "event": event,
            "predicted": predicted,
            "expected": expected,
            "distance": distance,
            "correct": correct,
        })


def add_access_event(participant, decision, reason, distance=None):
    with connect_db() as connection:
        connection.execute(
            "INSERT INTO access_log(participant, decision, reason, distance, event_time, operator) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                participant,
                decision,
                reason,
                distance,
                datetime.now().astimezone().isoformat(timespec="seconds"),
                session.get("username", ""),
            ),
        )


def safe_person_folder(name):
    clean = face_app.clean_name(name)
    folder = (face_app.DATA_DIR / clean).resolve()
    root = face_app.DATA_DIR.resolve()

    if root not in folder.parents:
        raise ValueError("Invalid participant name.")

    return clean, folder


@web_app.get("/health")
def health_check():
    return jsonify({"status": "ok"})


@web_app.get("/")
def home():
    if user_count() == 0:
        return redirect(url_for("setup"))
    if not session.get("user_id"):
        return redirect(url_for("login"))
    if session.get("role") == "member":
        participant_id = session.get("participant_id", "")
        display_name = participant_id.replace("_", " ")
        category = "Participant"
        try:
            _, folder = safe_person_folder(participant_id)
            if folder.is_dir():
                metadata = ensure_display_labels([folder], read_people_metadata())
                display_name = metadata[participant_id]["display_label"]
                category = metadata[participant_id].get("category", "Participant")
        except ValueError:
            pass
        return render_template(
            "member_home.html",
            current_username=session.get("username", "User"),
            participant_name=display_name,
            participant_category=category,
        )
    return render_template(
        "index.html",
        current_username=session.get("username", "User"),
        current_role=session.get("role", "operator"),
    )


@web_app.route("/setup", methods=["GET", "POST"])
def setup():
    if user_count() > 0:
        return redirect(url_for("login"))
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(username) < 3:
            error = "Username must be at least 3 characters."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            try:
                with connect_db() as connection:
                    cursor = connection.execute(
                        "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)",
                        (username, hash_password(password), datetime.now().astimezone().isoformat(timespec="seconds")),
                    )
                    session.update({"user_id": cursor.lastrowid, "username": username, "role": "admin"})
                return redirect(url_for("home"))
            except sqlite3.IntegrityError:
                error = "That username is already in use."
    return render_template("setup.html", error=error)


@web_app.route("/login", methods=["GET", "POST"])
def login():
    if user_count() == 0:
        return redirect(url_for("setup"))
    if session.get("user_id"):
        return redirect(url_for("home"))
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with connect_db() as connection:
            user = connection.execute(
                "SELECT id, username, password_hash, role FROM users WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
        if user and password_matches(password, user["password_hash"]):
            session.clear()
            session.update({"user_id": user["id"], "username": user["username"], "role": user["role"]})
            return redirect(url_for("home"))
        with connect_db() as connection:
            member = connection.execute(
                "SELECT id, username, password_hash, participant_id, institution_id "
                "FROM member_accounts WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
        if member and password_matches(password, member["password_hash"]):
            session.clear()
            session.update({
                "user_id": member["id"],
                "username": member["username"],
                "role": "member",
                "participant_id": member["participant_id"],
                "institution_id": member["institution_id"],
            })
            return redirect(url_for("home"))
        with connect_db() as connection:
            pending = connection.execute(
                "SELECT 1 FROM signup_requests WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
        if pending:
            error = "Your account request is waiting for Admin approval."
        else:
            error = "Username or password is incorrect."
    return render_template("login.html", error=error)


@web_app.route("/register", methods=["GET", "POST"])
def register():
    if user_count() == 0:
        return redirect(url_for("setup"))
    if session.get("user_id"):
        return redirect(url_for("home"))
    error = ""
    submitted = False
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        display_name = request.form.get("display_name", "").strip()
        category = request.form.get("category", "").strip()
        institution_id = request.form.get("institution_id", "").strip().upper()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(username) < 3:
            error = "Username must be at least 3 characters."
        elif len(display_name) < 2 or len(display_name) > 60:
            error = "Enter your full name (2–60 characters)."
        elif category not in {"Student", "Employee"}:
            error = "Choose Student or Employee."
        elif not institution_id or len(institution_id) > 40:
            error = "Enter your student or employee ID."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            with connect_db() as connection:
                occupied = connection.execute(
                    "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE "
                    "UNION SELECT 1 FROM member_accounts WHERE username = ? COLLATE NOCASE "
                    "UNION SELECT 1 FROM signup_requests WHERE username = ? COLLATE NOCASE",
                    (username, username, username),
                ).fetchone()
                if occupied:
                    error = "That username is already used or waiting for approval."
                else:
                    connection.execute(
                        "INSERT INTO signup_requests(username, password_hash, display_name, category, institution_id, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (username, hash_password(password), display_name, category, institution_id,
                         datetime.now().astimezone().isoformat(timespec="seconds")),
                    )
                    submitted = True
    return render_template("register.html", error=error, submitted=submitted)


@web_app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@web_app.route("/api/users", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "operator")
        if len(username) < 3:
            return jsonify({"error": "Username must be at least 3 characters."}), 400
        if len(password) < 8:
            return jsonify({"error": "Password must be at least 8 characters."}), 400
        if role not in {"admin", "operator"}:
            return jsonify({"error": "Choose Admin or Operator."}), 400
        try:
            with connect_db() as connection:
                existing = connection.execute(
                    "SELECT 1 FROM member_accounts WHERE username = ? COLLATE NOCASE "
                    "UNION SELECT 1 FROM signup_requests WHERE username = ? COLLATE NOCASE",
                    (username, username),
                ).fetchone()
                if existing:
                    return jsonify({"error": "That username is already used or waiting for approval."}), 400
                connection.execute(
                    "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                    (username, hash_password(password), role, datetime.now().astimezone().isoformat(timespec="seconds")),
                )
        except sqlite3.IntegrityError:
            return jsonify({"error": "That username is already in use."}), 400
        return jsonify({"message": "User account created."}), 201

    with connect_db() as connection:
        rows = connection.execute("SELECT username, role, created_at FROM users ORDER BY username").fetchall()
        members = connection.execute(
            "SELECT username, 'member' AS role, created_at FROM member_accounts ORDER BY username"
        ).fetchall()
    return jsonify({"users": [dict(row) for row in [*rows, *members]]})


@web_app.get("/api/signup-requests")
@admin_required
def signup_requests():
    with connect_db() as connection:
        requests_list = connection.execute(
            "SELECT id, username, display_name, category, institution_id, created_at "
            "FROM signup_requests ORDER BY id"
        ).fetchall()
    folders = []
    if face_app.DATA_DIR.exists():
        folders = sorted(folder for folder in face_app.DATA_DIR.iterdir() if folder.is_dir())
    metadata = ensure_display_labels(folders, read_people_metadata())
    participants = [
        {
            "id": folder.name,
            "name": metadata[folder.name]["display_label"],
            "category": metadata[folder.name].get("category", "Student"),
        }
        for folder in folders
        if metadata[folder.name].get("category") in {"Student", "Employee"}
    ]
    return jsonify({"requests": [dict(row) for row in requests_list], "participants": participants})


@web_app.put("/api/users/<username>/password")
@admin_required
def reset_user_password(username):
    password = request.form.get("password", "")
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400
    hashed = hash_password(password)
    with connect_db() as connection:
        staff = connection.execute(
            "UPDATE users SET password_hash = ? WHERE username = ? COLLATE NOCASE",
            (hashed, username),
        )
        if staff.rowcount == 0:
            member = connection.execute(
                "UPDATE member_accounts SET password_hash = ? WHERE username = ? COLLATE NOCASE",
                (hashed, username),
            )
            if member.rowcount == 0:
                return jsonify({"error": "Account was not found."}), 404
    return jsonify({"message": f"Password reset for {username}."})


@web_app.delete("/api/users/<username>")
@admin_required
def delete_user_account(username):
    if username.casefold() == session.get("username", "").casefold():
        return jsonify({"error": "You cannot delete the account you are currently using."}), 400
    with connect_db() as connection:
        staff = connection.execute(
            "SELECT id, role FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        if staff:
            if staff["role"] == "admin":
                admins = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin'"
                ).fetchone()[0]
                if admins <= 1:
                    return jsonify({"error": "The last Admin account cannot be deleted."}), 400
            connection.execute("DELETE FROM users WHERE id = ?", (staff["id"],))
        else:
            member = connection.execute(
                "DELETE FROM member_accounts WHERE username = ? COLLATE NOCASE", (username,)
            )
            if member.rowcount == 0:
                return jsonify({"error": "Account was not found."}), 404
    return jsonify({"message": f"Account {username} was deleted."})


@web_app.post("/api/signup-requests/<int:request_id>/approve")
@admin_required
def approve_signup_request(request_id):
    participant_id = request.form.get("participant_id", "").strip()
    try:
        clean, folder = safe_person_folder(participant_id)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if not folder.is_dir():
        return jsonify({"error": "Choose an enrolled participant."}), 404
    metadata = ensure_display_labels([folder], read_people_metadata())
    with connect_db() as connection:
        request_row = connection.execute(
            "SELECT * FROM signup_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if not request_row:
            return jsonify({"error": "Signup request was not found."}), 404
        if request_row["category"] != metadata[clean].get("category"):
            return jsonify({"error": "The selected participant category does not match this request."}), 400
        if request_row["display_name"].strip().casefold() != metadata[clean]["display_label"].strip().casefold():
            return jsonify({"error": "The selected participant name does not match. Verify the request and enrolled person."}), 400
        try:
            connection.execute(
                "INSERT INTO member_accounts(username, password_hash, participant_id, institution_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (request_row["username"], request_row["password_hash"], clean,
                 request_row["institution_id"], datetime.now().astimezone().isoformat(timespec="seconds")),
            )
        except sqlite3.IntegrityError:
            return jsonify({"error": "This username or participant already has an account."}), 409
        connection.execute("DELETE FROM signup_requests WHERE id = ?", (request_id,))
    return jsonify({"message": f"Account approved for {metadata[clean]['display_label']}. They can now sign in."})


@web_app.delete("/api/signup-requests/<int:request_id>")
@admin_required
def reject_signup_request(request_id):
    with connect_db() as connection:
        result = connection.execute("DELETE FROM signup_requests WHERE id = ?", (request_id,))
        if result.rowcount == 0:
            return jsonify({"error": "Signup request was not found."}), 404
    return jsonify({"message": "Signup request rejected."})


@web_app.get("/api/status")
@staff_required
def status():
    folders = []
    if face_app.DATA_DIR.exists():
        folders = sorted(
            folder for folder in face_app.DATA_DIR.iterdir()
            if folder.is_dir()
        )

    participants = []
    metadata = ensure_display_labels(folders, read_people_metadata())
    for folder in folders:
        image_total = len(list(folder.glob("*.png")))
        person_info = metadata.get(folder.name, {})
        participants.append({
            "id": folder.name,
            "name": person_info["display_label"],
            "category": person_info.get("category", "Participant"),
            "access_authorized": bool(person_info.get("access_authorized", False)),
            "images": image_total,
            "quality_ok": image_total >= MIN_IMAGES_PER_PERSON,
            "minimum_images": MIN_IMAGES_PER_PERSON,
            "quality_target": IMAGE_TARGET,
        })

    evaluations = [
        row for row in read_history_rows()
        if row.get("event") == "evaluation"
    ]
    correct_total = sum(
        1 for row in evaluations if row.get("correct") == "yes"
    )
    evaluation_total = len(evaluations)
    accuracy = (
        round(100 * correct_total / evaluation_total, 1)
        if evaluation_total
        else None
    )

    return jsonify({
        "participants": participants,
        "model_ready": (
            face_app.MODEL_PATH.exists()
            and face_app.LABELS_PATH.exists()
        ),
        "evaluation": {
            "total": evaluation_total,
            "correct": correct_total,
            "accuracy": accuracy,
        },
    })


@web_app.get("/api/access")
@staff_required
def access_status():
    folders = []
    if face_app.DATA_DIR.exists():
        folders = sorted(
            folder for folder in face_app.DATA_DIR.iterdir()
            if folder.is_dir()
        )
    metadata = ensure_display_labels(folders, read_people_metadata())
    participants = [
        {
            "id": folder.name,
            "name": metadata[folder.name]["display_label"],
            "category": metadata[folder.name].get("category", "Participant"),
            "authorized": bool(metadata[folder.name].get("access_authorized", False)),
            "pass_required": metadata[folder.name].get("category") == "Visitor",
        }
        for folder in folders
    ]
    with connect_db() as connection:
        rows = connection.execute(
            "SELECT participant, decision, reason, distance, event_time, operator "
            "FROM access_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
    return jsonify({"participants": participants, "logs": [dict(row) for row in rows]})


@web_app.route("/api/visitor-passes", methods=["GET", "POST"])
@login_required
def visitor_passes():
    role = session.get("role")
    if request.method == "GET":
        with connect_db() as connection:
            if role == "member":
                rows = connection.execute(
                    "SELECT * FROM visitor_passes WHERE host_username = ? COLLATE NOCASE ORDER BY id DESC LIMIT 200",
                    (session.get("username", ""),),
                ).fetchall()
                host_options = []
            elif role in {"admin", "operator"}:
                rows = connection.execute(
                    "SELECT * FROM visitor_passes ORDER BY id DESC LIMIT 200"
                ).fetchall()
                host_options = []
                staff = connection.execute("SELECT username, role FROM users ORDER BY username").fetchall()
                for account in staff:
                    host_options.append({"username": account["username"], "label": f"{account['username']} · {account['role'].title()}"})
                members = connection.execute(
                    "SELECT ma.username, ma.participant_id FROM member_accounts ma ORDER BY ma.username"
                ).fetchall()
                people = read_people_metadata()
                for account in members:
                    person = people.get(account["participant_id"], {})
                    label = person.get("display_label", account["participant_id"].replace("_", " "))
                    host_options.append({"username": account["username"], "label": f"{label} · {account['username']}"})
            else:
                return jsonify({"error": "This account cannot view visitor passes."}), 403
        metadata = read_people_metadata()
        passes = []
        for row in rows:
            item = dict(row)
            person = metadata.get(item["participant_id"], {})
            item["visitor_name"] = person.get("display_label", item["participant_id"].replace("_", " "))
            passes.append(item)
        visitor_options = []
        if role in {"admin", "operator"} and face_app.DATA_DIR.exists():
            for folder in sorted(face_app.DATA_DIR.iterdir()):
                if folder.is_dir() and metadata.get(folder.name, {}).get("category") == "Visitor":
                    visitor_options.append({"id": folder.name, "name": metadata[folder.name].get("display_label", folder.name)})
        return jsonify({"passes": passes, "hosts": host_options, "visitors": visitor_options, "role": role})

    if role not in {"admin", "operator"}:
        return jsonify({"error": "Only project staff can create visitor passes."}), 403
    participant_id = request.form.get("participant_id", "").strip()
    host_username = request.form.get("host_username", "").strip()
    purpose = request.form.get("purpose", "").strip()
    try:
        starts = datetime.fromisoformat(request.form.get("starts_at", ""))
        ends = datetime.fromisoformat(request.form.get("ends_at", ""))
        if starts.tzinfo is None:
            starts = starts.astimezone()
        if ends.tzinfo is None:
            ends = ends.astimezone()
    except ValueError:
        return jsonify({"error": "Enter valid visit start and end times."}), 400
    if not purpose or len(purpose) > 160:
        return jsonify({"error": "Enter a visit purpose (up to 160 characters)."}), 400
    if ends <= starts:
        return jsonify({"error": "The end time must be after the start time."}), 400
    folders = list(face_app.DATA_DIR.iterdir()) if face_app.DATA_DIR.exists() else []
    metadata = ensure_display_labels(folders, read_people_metadata())
    if participant_id not in {folder.name for folder in folders} or metadata.get(participant_id, {}).get("category") != "Visitor":
        return jsonify({"error": "Choose an enrolled participant categorized as Visitor."}), 400
    with connect_db() as connection:
        host = connection.execute(
            "SELECT username FROM member_accounts WHERE username = ? COLLATE NOCASE",
            (host_username,),
        ).fetchone()
        if not host:
            return jsonify({"error": "Choose an existing host account."}), 400
        connection.execute(
            "INSERT INTO visitor_passes(participant_id, host_username, purpose, starts_at, ends_at, status, requested_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (participant_id, host["username"], purpose, starts.isoformat(timespec="minutes"), ends.isoformat(timespec="minutes"),
             session.get("username", ""), datetime.now().astimezone().isoformat(timespec="seconds")),
        )
    return jsonify({"message": "Visitor pass request sent to the host for approval."}), 201


@web_app.post("/api/visitor-passes/<int:pass_id>/decision")
@login_required
def decide_visitor_pass(pass_id):
    decision = request.form.get("decision", "").strip().lower()
    if decision not in {"approved", "rejected"}:
        return jsonify({"error": "Choose approve or reject."}), 400
    with connect_db() as connection:
        item = connection.execute("SELECT * FROM visitor_passes WHERE id = ?", (pass_id,)).fetchone()
        if not item:
            return jsonify({"error": "Visitor pass was not found."}), 404
        is_admin = session.get("role") == "admin"
        is_host = session.get("role") in {"member", "operator"} and session.get("username", "").casefold() == item["host_username"].casefold()
        if not (is_admin or is_host):
            return jsonify({"error": "Only the assigned host or an Admin can decide this request."}), 403
        if item["status"] != "pending":
            return jsonify({"error": "This visitor request has already been decided."}), 409
        pass_code = secrets.token_hex(4).upper() if decision == "approved" else None
        connection.execute(
            "UPDATE visitor_passes SET status = ?, pass_code = ?, decided_by = ?, decided_at = ? WHERE id = ?",
            (decision, pass_code, session.get("username", ""), datetime.now().astimezone().isoformat(timespec="seconds"), pass_id),
        )
    message = "Visitor pass approved." if decision == "approved" else "Visitor request rejected."
    return jsonify({"message": message, "pass_code": pass_code})


def active_visitor_pass(participant_id, now=None):
    now = now or datetime.now().astimezone()
    with connect_db() as connection:
        rows = connection.execute(
            "SELECT pass_code, starts_at, ends_at FROM visitor_passes "
            "WHERE participant_id = ? AND status = 'approved' ORDER BY id DESC",
            (participant_id,),
        ).fetchall()
    for row in rows:
        try:
            starts = datetime.fromisoformat(row["starts_at"])
            ends = datetime.fromisoformat(row["ends_at"])
            if starts <= now <= ends:
                return row["pass_code"]
        except ValueError:
            continue
    return None


@web_app.post("/api/participants/<name>/access")
@admin_required
def set_access_permission(name):
    try:
        clean, folder = safe_person_folder(name)
        if not folder.is_dir():
            return jsonify({"error": "Participant folder was not found."}), 404
        requested = request.form.get("authorized", "").strip().lower()
        if requested not in {"true", "false"}:
            return jsonify({"error": "Choose whether access is allowed."}), 400

        metadata = ensure_display_labels(
            [folder], read_people_metadata()
        )
        if metadata[clean].get("category") == "Visitor":
            return jsonify({"error": "Visitor access is controlled by approved, time-limited passes."}), 400
        metadata[clean]["access_authorized"] = requested == "true"
        save_people_metadata(metadata)
        state = "authorized" if requested == "true" else "not authorized"
        return jsonify({
            "message": f"{metadata[clean]['display_label']} is now {state} for the access demo.",
            "authorized": requested == "true",
        })
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@web_app.post("/api/enroll")
@admin_required
def enroll():
    try:
        display_name = request.form.get("name", "").strip()
        name = face_app.clean_name(display_name)
        if len(display_name) > 60:
            return jsonify({"error": "Participant name must be 60 characters or fewer."}), 400
        category = request.form.get("category", "Student").strip()
        if category not in PERSON_TYPES:
            return jsonify({"error": "Choose Student, Employee, Visitor, or Security staff."}), 400
        folders = list(face_app.DATA_DIR.iterdir()) if face_app.DATA_DIR.exists() else []
        metadata = ensure_display_labels(folders, read_people_metadata())
        image = read_uploaded_image()

        if image is None:
            return jsonify({"error": "Take or upload a photo first."}), 400

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        detector = face_app.get_detector()
        faces = detector.detectMultiScale(
            gray,
            scaleFactor=1.2,
            minNeighbors=5,
            minSize=(60, 60),
        )

        if len(faces) == 0:
            return jsonify({"error": "No face detected. Try another photo."}), 400

        x, y, width, height = max(faces, key=lambda box: box[2] * box[3])
        face = gray[y:y + height, x:x + width]
        face = cv2.resize(face, face_app.IMAGE_SIZE)

        folder = face_app.DATA_DIR / name
        folder.mkdir(parents=True, exist_ok=True)

        numbers = [
            int(path.stem)
            for path in folder.glob("*.png")
            if path.stem.isdigit()
        ]
        next_number = max(numbers, default=0) + 1
        output_path = folder / f"{next_number:04d}.png"

        if not cv2.imwrite(str(output_path), face):
            return jsonify({"error": "Could not save the face image."}), 500

        metadata[name] = {
            "category": category,
            "display_name": display_name,
            "display_label": display_name,
            "access_authorized": bool(
                metadata.get(name, {}).get("access_authorized", False)
            ),
        }
        save_people_metadata(metadata)
        # New samples change the dataset, so require a fresh model before recognition.
        face_app.MODEL_PATH.unlink(missing_ok=True)
        face_app.LABELS_PATH.unlink(missing_ok=True)

        return jsonify({
            "message": f"Saved image {next_number} for {display_name}.",
            "name": display_name,
            "category": category,
            "images": next_number,
        })

    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except (RuntimeError, cv2.error) as error:
        return jsonify({"error": str(error)}), 500


@web_app.post("/api/delete")
@admin_required
def delete_participant():
    try:
        name, folder = safe_person_folder(request.form.get("name", ""))

        if not folder.is_dir():
            return jsonify({"error": "Participant folder was not found."}), 404

        shutil.rmtree(folder)

        metadata = read_people_metadata()
        metadata.pop(name, None)
        save_people_metadata(metadata)

        # The model contains this participant, so remove it and require retraining.
        face_app.MODEL_PATH.unlink(missing_ok=True)
        face_app.LABELS_PATH.unlink(missing_ok=True)

        return jsonify({"message": "Participant data deleted. Retrain the model."})

    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@web_app.get("/api/participants/<name>/images")
@admin_required
def participant_images(name):
    try:
        clean, folder = safe_person_folder(name)
        if not folder.is_dir():
            return jsonify({"error": "Participant folder was not found."}), 404

        images = sorted(path.name for path in folder.glob("*.png"))
        return jsonify({"name": clean, "images": images})

    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@web_app.get("/api/participants/<name>/images/<filename>")
@admin_required
def participant_image(name, filename):
    try:
        clean, folder = safe_person_folder(name)
        safe_filename = Path(filename).name

        if safe_filename != filename or not safe_filename.lower().endswith(".png"):
            return jsonify({"error": "Invalid image name."}), 400

        image_path = folder / safe_filename
        if not image_path.is_file():
            return jsonify({"error": "Image not found."}), 404

        return send_file(image_path, mimetype="image/png")

    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@web_app.post("/api/train")
@admin_required
def train():
    try:
        face_app.train_model()
        return jsonify({"message": "Training complete. The model is ready."})
    except (RuntimeError, cv2.error) as error:
        return jsonify({"error": str(error)}), 400


@web_app.post("/api/recognize")
@staff_required
def recognize():
    if not face_app.MODEL_PATH.exists() or not face_app.LABELS_PATH.exists():
        return jsonify({"error": "Train the model before recognition."}), 400

    try:
        image = read_uploaded_image()
        if image is None:
            return jsonify({"error": "Take or upload a photo first."}), 400

        threshold = float(request.form.get("threshold", "70"))
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        detector = face_app.get_detector()
        faces = detector.detectMultiScale(
            gray,
            scaleFactor=1.2,
            minNeighbors=5,
            minSize=(60, 60),
        )

        if len(faces) == 0:
            return jsonify({
                "message": "No face detected. Try another photo.",
                "results": [],
            })

        recognizer = cv2.face.LBPHFaceRecognizer_create()
        recognizer.read(str(face_app.MODEL_PATH))
        labels = json.loads(
            face_app.LABELS_PATH.read_text(encoding="utf-8")
        )

        results = []
        folders = list(face_app.DATA_DIR.iterdir()) if face_app.DATA_DIR.exists() else []
        metadata = ensure_display_labels(folders, read_people_metadata())
        display_names = {
            internal_name: person_info["display_label"]
            for internal_name, person_info in metadata.items()
        }
        recognized_ids = []
        for (x, y, width, height) in faces:
            face = gray[y:y + height, x:x + width]
            face = cv2.resize(face, face_app.IMAGE_SIZE)
            label_id, distance = recognizer.predict(face)
            participant_id = None
            access_authorized = False
            access_reason = "Face was not recognized."

            if distance <= threshold:
                internal_name = labels.get(str(label_id), "Unknown")
                name = display_names.get(internal_name, "Unknown")
                if name != "Unknown":
                    participant_id = internal_name
                    recognized_ids.append(internal_name)
                    person_category = metadata.get(internal_name, {}).get("category", "")
                    if person_category == "Visitor":
                        active_code = active_visitor_pass(internal_name)
                        access_authorized = active_code is not None
                        access_reason = (
                            f"Approved visitor pass active · {active_code}"
                            if active_code
                            else "No approved visitor pass is active now"
                        )
                    else:
                        access_authorized = bool(
                            metadata.get(internal_name, {}).get("access_authorized", False)
                        )
                        access_reason = (
                            "Authorized participant"
                            if access_authorized
                            else "Participant is not authorized"
                        )
                color = (0, 190, 0)
            else:
                name = "Unknown"
                color = (0, 140, 255)

            distance = round(float(distance), 1)
            access_decision = "granted" if access_authorized else "denied"
            results.append({
                "name": name,
                "participant_id": participant_id,
                "distance": distance,
                "access_decision": access_decision,
                "access_reason": access_reason,
            })
            add_history("recognition", predicted=name, distance=distance)
            add_access_event(
                name,
                access_decision,
                access_reason,
                distance,
            )

            cv2.rectangle(
                image,
                (x, y),
                (x + width, y + height),
                color,
                2,
            )
            cv2.putText(
                image,
                name,
                (x, max(25, y - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                color,
                2,
            )

        session["last_recognized"] = {
            participant_id: time.time() for participant_id in set(recognized_ids)
        }

        success, encoded = cv2.imencode(".jpg", image)
        if not success:
            return jsonify({"error": "Could not create the result image."}), 500

        return jsonify({
            "results": results,
            "image": base64.b64encode(encoded).decode("ascii"),
        })

    except (ValueError, RuntimeError, cv2.error, OSError) as error:
        return jsonify({"error": str(error)}), 400


@web_app.post("/api/evaluate")
@staff_required
def evaluate():
    predicted = request.form.get("predicted", "").strip()
    expected = request.form.get("expected", "").strip()
    distance = request.form.get("distance", "").strip()

    if not predicted or not expected:
        return jsonify({"error": "Choose the actual participant label."}), 400

    correct = predicted == expected
    add_history(
        "evaluation",
        predicted=predicted,
        expected=expected,
        distance=distance,
        correct="yes" if correct else "no",
    )

    return jsonify({
        "message": "Evaluation saved.",
        "correct": correct,
    })


@web_app.route("/api/attendance", methods=["GET", "POST"])
@staff_required
def attendance():
    if request.method == "POST":
        try:
            participant = face_app.clean_name(request.form.get("participant", ""))
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        event = request.form.get("event", "")
        if event not in {"check_in", "check_out"}:
            return jsonify({"error": "Choose check in or check out."}), 400
        _, folder = safe_person_folder(participant)
        if not folder.is_dir():
            return jsonify({"error": "That person is not enrolled."}), 404
        recognized_at = session.get("last_recognized", {}).get(participant)
        if not recognized_at or time.time() - recognized_at > 120:
            return jsonify({"error": "Recognize this person first, then record attendance within 2 minutes."}), 403

        recorded_at = datetime.now().astimezone()
        today = recorded_at.date().isoformat()
        with connect_db() as connection:
            previous = connection.execute(
                "SELECT event FROM attendance WHERE participant = ? AND substr(event_time, 1, 10) = ? ORDER BY id DESC LIMIT 1",
                (participant, today),
            ).fetchone()
            if event == "check_in" and previous and previous["event"] == "check_in":
                return jsonify({"error": "This person is already checked in today."}), 409
            if event == "check_out" and (not previous or previous["event"] != "check_in"):
                return jsonify({"error": "Check in must be recorded before check out."}), 409
            connection.execute(
                "INSERT INTO attendance(participant, event, event_time, operator) VALUES (?, ?, ?, ?)",
                (participant, event, recorded_at.isoformat(timespec="seconds"), session.get("username", "")),
            )
        session["last_recognized"].pop(participant, None)
        metadata = ensure_display_labels([folder], read_people_metadata())
        display_name = metadata[participant]["display_label"]
        event_label = event.replace("_", " ").title()
        return jsonify({
            "message": f"Attendance recorded: {display_name} · {event_label} · {recorded_at.strftime('%Y-%m-%d %H:%M:%S')}",
            "participant": display_name,
            "event": event,
            "date": recorded_at.date().isoformat(),
            "time": recorded_at.strftime("%H:%M:%S"),
        })

    selected_date = request.args.get("date", "").strip()
    person_filter = request.args.get("person", "").strip()
    query = "SELECT id, participant, event, event_time, operator FROM attendance WHERE 1=1"
    parameters = []
    if selected_date:
        query += " AND substr(event_time, 1, 10) = ?"
        parameters.append(selected_date)
    if person_filter:
        query += " AND participant LIKE ?"
        parameters.append(f"%{person_filter}%")
    query += " ORDER BY id DESC LIMIT 500"
    with connect_db() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return jsonify({"rows": [dict(row) for row in rows]})


@web_app.get("/api/attendance/summary")
@staff_required
def attendance_summary():
    today = datetime.now().astimezone().date().isoformat()
    folders = []
    if face_app.DATA_DIR.exists():
        folders = sorted(folder for folder in face_app.DATA_DIR.iterdir() if folder.is_dir())
    metadata = ensure_display_labels(folders, read_people_metadata())
    with connect_db() as connection:
        records = connection.execute(
            "SELECT participant, event, event_time FROM attendance "
            "WHERE substr(event_time, 1, 10) = ? ORDER BY id",
            (today,),
        ).fetchall()

    latest_by_person = {}
    checked_in = set()
    for record in records:
        latest_by_person[record["participant"]] = record
        if record["event"] == "check_in":
            checked_in.add(record["participant"])

    people = []
    for folder in folders:
        last = latest_by_person.get(folder.name)
        people.append({
            "name": metadata[folder.name]["display_label"],
            "category": metadata[folder.name].get("category", "Participant"),
            "status": "Present" if folder.name in checked_in else "Not attended",
            "last_event": last["event"].replace("_", " ").title() if last else "—",
            "event_time": last["event_time"] if last else "—",
        })
    return jsonify({"date": today, "people": people})


@web_app.get("/api/my-attendance")
@member_required
def my_attendance():
    participant_id = session["participant_id"]
    today = datetime.now().astimezone().date().isoformat()
    with connect_db() as connection:
        rows = connection.execute(
            "SELECT event, event_time FROM attendance WHERE participant = ? "
            "ORDER BY id DESC LIMIT 100",
            (participant_id,),
        ).fetchall()
    today_rows = [row for row in rows if row["event_time"][:10] == today]
    checked_in = any(row["event"] == "check_in" for row in today_rows)
    check_in_row = next((row for row in reversed(today_rows) if row["event"] == "check_in"), None)
    folder = face_app.DATA_DIR / participant_id
    display_name = participant_id.replace("_", " ")
    if folder.is_dir():
        metadata = ensure_display_labels([folder], read_people_metadata())
        display_name = metadata[participant_id]["display_label"]
    return jsonify({
        "date": today,
        "name": display_name,
        "status": "Attended" if checked_in else "Not attended",
        "check_in_time": check_in_row["event_time"] if check_in_row else None,
        "records": [dict(row) for row in rows],
    })


def monthly_attendance_rows(month):
    folders = []
    if face_app.DATA_DIR.exists():
        folders = sorted(folder for folder in face_app.DATA_DIR.iterdir() if folder.is_dir())
    metadata = ensure_display_labels(folders, read_people_metadata())
    with connect_db() as connection:
        records = connection.execute(
            "SELECT participant, "
            "SUM(CASE WHEN event = 'check_in' THEN 1 ELSE 0 END) AS check_ins, "
            "SUM(CASE WHEN event = 'check_out' THEN 1 ELSE 0 END) AS check_outs, "
            "COUNT(DISTINCT CASE WHEN event = 'check_in' THEN substr(event_time, 1, 10) END) AS days_attended, "
            "MAX(event_time) AS last_event "
            "FROM attendance WHERE substr(event_time, 1, 7) = ? GROUP BY participant",
            (month,),
        ).fetchall()
    totals = {row["participant"]: row for row in records}
    rows = []
    for folder in folders:
        record = totals.get(folder.name)
        rows.append({
            "participant": metadata[folder.name]["display_label"],
            "category": metadata[folder.name].get("category", "Participant"),
            "check_ins": record["check_ins"] if record else 0,
            "check_outs": record["check_outs"] if record else 0,
            "days_attended": record["days_attended"] if record else 0,
            "last_event": record["last_event"] if record and record["last_event"] else "—",
        })
    return rows


@web_app.get("/api/attendance/monthly")
@staff_required
def monthly_attendance():
    month = request.args.get("month", datetime.now().astimezone().strftime("%Y-%m"))
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        return jsonify({"error": "Choose a valid month."}), 400
    rows = monthly_attendance_rows(month)
    return jsonify({
        "month": month,
        "participants": len(rows),
        "people_attended": sum(row["days_attended"] > 0 for row in rows),
        "total_check_ins": sum(row["check_ins"] for row in rows),
        "total_check_outs": sum(row["check_outs"] for row in rows),
        "rows": rows,
    })


@web_app.get("/api/attendance/monthly.csv")
@staff_required
def download_monthly_attendance():
    month = request.args.get("month", datetime.now().astimezone().strftime("%Y-%m"))
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        return jsonify({"error": "Choose a valid month."}), 400
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["Month", "Participant", "Category", "Days attended", "Check-ins", "Check-outs", "Latest event"])
    for row in monthly_attendance_rows(month):
        writer.writerow([month, row["participant"], row["category"], row["days_attended"], row["check_ins"], row["check_outs"], row["last_event"]])
    return send_file(
        io.BytesIO(buffer.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"attendance_{month}.csv",
    )


@web_app.get("/api/backup.zip")
@admin_required
def download_project_backup():
    archive_buffer = io.BytesIO()
    memory_db = sqlite3.connect(":memory:")
    try:
        with connect_db() as source_db:
            source_db.backup(memory_db)
        database_bytes = memory_db.serialize()
    finally:
        memory_db.close()

    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project_users.sqlite3", database_bytes)
        for path, archive_name in (
            (PEOPLE_META_PATH, "people_metadata.json"),
            (HISTORY_PATH, "recognition_history.csv"),
            (face_app.CASCADE_PATH, "haarcascade_frontalface_default.xml"),
        ):
            if path.is_file():
                archive.write(path, archive_name)
        for folder_path in (face_app.DATA_DIR, face_app.MODEL_DIR):
            if folder_path.exists():
                for path in folder_path.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(face_app.APP_DATA_DIR).as_posix())
    archive_buffer.seek(0)
    return send_file(
        archive_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"face_recognition_backup_{datetime.now().astimezone().date().isoformat()}.zip",
    )


@web_app.delete("/api/attendance/<int:record_id>")
@admin_required
def delete_attendance(record_id):
    with connect_db() as connection:
        result = connection.execute(
            "DELETE FROM attendance WHERE id = ?",
            (record_id,),
        )
        if result.rowcount == 0:
            return jsonify({"error": "Attendance record was not found."}), 404
    return jsonify({"message": "Attendance record deleted."})


@web_app.get("/api/attendance.csv")
@staff_required
def download_attendance():
    with connect_db() as connection:
        rows = connection.execute(
            "SELECT event_time, participant, event, operator FROM attendance ORDER BY id DESC"
        ).fetchall()
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["Time", "Person", "Event", "Recorded by"])
    for row in rows:
        writer.writerow([row["event_time"], row["participant"], row["event"], row["operator"]])
    return send_file(
        io.BytesIO(buffer.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="attendance_report.csv",
    )


@web_app.get("/api/history")
@staff_required
def history():
    rows = read_history_rows()
    first_recent_index = max(0, len(rows) - 200)
    recent_rows = [
        {**rows[index], "row_id": index}
        for index in range(len(rows) - 1, first_recent_index - 1, -1)
    ]
    return jsonify({"rows": recent_rows})


@web_app.delete("/api/history/<int:row_id>")
@admin_required
def delete_history_row(row_id):
    rows = read_history_rows()
    if row_id < 0 or row_id >= len(rows):
        return jsonify({"error": "History record was not found."}), 404

    del rows[row_id]
    with HISTORY_PATH.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=HISTORY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return jsonify({"message": "History record deleted."})


@web_app.get("/api/history.csv")
@staff_required
def download_history():
    if not HISTORY_PATH.exists():
        add_history("history_started")

    # History is generated only after a recognition attempt or evaluation.
    rows = read_history_rows()
    sanitized = APP_DATA_DIR / "history_export.csv"
    with sanitized.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=HISTORY_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return send_file(
        sanitized,
        mimetype="text/csv",
        as_attachment=True,
        download_name="recognition_history.csv",
    )


if __name__ == "__main__":
    web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
