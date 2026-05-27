from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os
import json
import io
import base64
import secrets
import re
from datetime import datetime, timedelta
from functools import wraps
from uuid import uuid4

import pyotp
import qrcode
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "uniwise_secret_key_123"

# ============================================================
# MOBILE ACCESS CONFIGURATION
# ============================================================
@app.after_request
def add_mobile_headers(response):
    """Enable mobile access from any device on the network"""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return response

@app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    return '', 204

MAX_ATTEMPTS = 5

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
RESOURCES_FILE = os.path.join(BASE_DIR, "resources_db.json")
USERS_FILE     = os.path.join(BASE_DIR, "users.json")
LOGS_FILE      = os.path.join(BASE_DIR, "chat_logs.json")
DICT_FILE      = os.path.join(BASE_DIR, "dictionary.json")
DEVICES_FILE   = os.path.join(BASE_DIR, "devices.json")
FAQ_DATA_FILE  = os.path.join(BASE_DIR, "faq_data.json")

UPLOAD_FOLDER      = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp",
    "pdf", "doc", "docx", "ppt", "pptx",
    "xls", "xlsx", "txt", "zip", "rar",
    "mp4", "mov", "webm", "m4v"
}

app.config["UPLOAD_FOLDER"]        = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"]   = 150 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# =========================
# FILE HELPERS
# =========================
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_json_file(path, default_data):
    if not os.path.exists(path):
        save_json_file(path, default_data)
        return default_data
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default_data


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


# =========================
# RESOURCES HELPERS
# =========================
def infer_attachment_type(file_name="", media_url=""):
    source      = f"{file_name} {media_url}".lower()
    image_exts  = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg")
    video_exts  = (".mp4", ".mov", ".webm", ".m4v")
    if source.endswith(image_exts) or any(ext in source for ext in image_exts):
        return "image"
    if source.endswith(video_exts) or any(ext in source for ext in video_exts):
        return "video"
    return "file"


def normalize_post_structure(post):
    post        = post or {}
    attachments = post.get("attachments", [])
    if not isinstance(attachments, list):
        attachments = []

    normalized_attachments = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        normalized_attachments.append({
            "type": item.get("type") or infer_attachment_type(item.get("name", ""), item.get("url", "")),
            "url":  item.get("url", ""),
            "name": item.get("name", "Attachment")
        })

    media_url  = post.get("mediaUrl", "")
    media_type = post.get("mediaType", "")
    file_name  = post.get("fileName", "")

    if media_url:
        exists = any(item.get("url") == media_url for item in normalized_attachments)
        if not exists:
            normalized_attachments.append({
                "type": media_type if media_type else infer_attachment_type(file_name, media_url),
                "url":  media_url,
                "name": file_name or "Attachment"
            })

    return {
        "id":           post.get("id", uuid4().hex),
        "type":         post.get("type", "upload"),
        "title":        post.get("title", ""),
        "body":         post.get("body", ""),
        "extra":        post.get("extra", ""),
        "caption":      post.get("caption", post.get("body", "")),
        "author":       post.get("author", "Admin"),
        "posted_by":    post.get("posted_by", post.get("author", "Admin")),
        "content_type": post.get("content_type", "mixed"),
        "attachments":  normalized_attachments,
        "created_at":   post.get("created_at", now_str()),
        "updated_at":   post.get("updated_at", post.get("created_at", now_str()))
    }


def serialize_post_for_admin(post):
    post        = normalize_post_structure(post)
    attachments = post.get("attachments", [])
    images      = [item for item in attachments if item.get("type") == "image"]
    videos      = [item for item in attachments if item.get("type") == "video"]
    files       = [item for item in attachments if item.get("type") == "file"]

    return {
        "id":           post.get("id"),
        "type":         post.get("type", "upload"),
        "post_type":    post.get("type", "upload"),
        "title":        post.get("title", ""),
        "body":         post.get("body", ""),
        "extra":        post.get("extra", ""),
        "caption":      post.get("caption", ""),
        "author":       post.get("author", "Admin"),
        "posted_by":    post.get("posted_by", post.get("author", "Admin")),
        "poster_role":  post.get("posted_by", post.get("author", "Admin")),
        "content_type": post.get("content_type", "text"),
        "attachments":  attachments,
        "images":       images,
        "videos":       videos,
        "files":        files,
        "created_at":   post.get("created_at", ""),
        "updated_at":   post.get("updated_at", "")
    }


def load_resources():
    default_data = {
        "announcement": {"title": "", "body": "", "extra": ""},
        "updates":      [],
        "contact":      {"phone": "", "email": "", "location": ""},
        "school": {
            "name": "", "destination": "", "map_embed": "",
            "google_maps_search": "", "coordinates": {"lat": 0, "lon": 0}
        },
        "links":        {"website": "", "facebook": ""},
        "about":        {"title": "About UniWise", "text1": "", "text2": ""},
        "hero_slider":  {"items": []},
        "posts":        []
    }

    data = load_json_file(RESOURCES_FILE, default_data)
    if not isinstance(data, dict):
        data = default_data

    for key, default_value in default_data.items():
        if key not in data:
            data[key] = default_value

    data["posts"] = [normalize_post_structure(p) for p in data.get("posts", [])]

    hero_slider = data.get("hero_slider", {})
    if not isinstance(hero_slider, dict):
        hero_slider = {"items": []}
    items = hero_slider.get("items", [])
    if not isinstance(items, list):
        items = []

    normalized_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized_items.append({
            "id":       item.get("id", uuid4().hex),
            "type":     item.get("type", infer_attachment_type(item.get("name", ""), item.get("url", ""))),
            "url":      item.get("url", ""),
            "name":     item.get("name", "LED Media"),
            "duration": int(item.get("duration", 7000) or 7000),
            "active":   bool(item.get("active", True))
        })

    data["hero_slider"] = {"items": normalized_items}
    return data


def save_resources(data):
    save_json_file(RESOURCES_FILE, data)


# =========================
# USERS / LOGS / DICTIONARY
# =========================
def load_users():
    default_users = {
        "admins": [{"username": "admin", "password": "admin123", "totp_secret": ""}]
    }
    return load_json_file(USERS_FILE, default_users)


def save_users(data):
    save_json_file(USERS_FILE, data)


def load_logs():
    return load_json_file(LOGS_FILE, [])


def save_logs(data):
    save_json_file(LOGS_FILE, data)


def load_dictionary():
    return load_json_file(DICT_FILE, {})


# =========================
# FAQ HELPERS
# =========================
def load_faq_data():
    return load_json_file(FAQ_DATA_FILE, {"questions": []})


def save_faq_data(data):
    save_json_file(FAQ_DATA_FILE, data)


def normalize_question(text):
    text = str(text or "").strip().lower()
    text = " ".join(text.split())
    text = re.sub(r"[^\w\s]", "", text)
    return text.strip()


def sanitize_question(text):
    return str(text or "").strip()


def make_question_id():
    return f"faq_{uuid4().hex[:12]}"


def find_question_by_id(questions, faq_id):
    for item in questions:
        if str(item.get("id", "")) == str(faq_id):
            return item
    return None


def get_sorted_all_questions(questions):
    return sorted(
        questions,
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True
    )


def get_top_faqs(questions, limit=10):
    approved = [item for item in questions if item.get("status") == "approved"]
    approved_sorted = sorted(
        approved,
        key=lambda item: (int(item.get("count", 0)), item.get("updated_at") or item.get("created_at") or ""),
        reverse=True
    )
    top_items = []
    for index, item in enumerate(approved_sorted[:limit], start=1):
        copied = dict(item)
        copied["rank"] = index
        top_items.append(copied)
    return top_items


def get_new_questions(questions):
    new_items = [item for item in questions if item.get("status") == "new"]
    return sorted(
        new_items,
        key=lambda item: (int(item.get("count", 0)), item.get("updated_at") or item.get("created_at") or ""),
        reverse=True
    )


def build_faq_insights_payload(questions):
    return {
        "top_faqs":      get_top_faqs(questions),
        "new_questions": get_new_questions(questions),
        "all_questions": get_sorted_all_questions(questions)
    }


# =========================
# DEVICE HELPERS
# =========================
def load_devices():
    return load_json_file(DEVICES_FILE, {"devices": []})


def save_devices(data):
    save_json_file(DEVICES_FILE, data)


def get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "Unknown"


def get_device_name():
    return request.headers.get("User-Agent", "Unknown device")[:180]


def register_logged_in_device(username, remember_device=False):
    devices_data   = load_devices()
    devices        = devices_data.get("devices", [])
    session_id     = secrets.token_hex(16)
    trusted_until  = None

    if remember_device:
        trusted_until = (datetime.utcnow() + timedelta(days=30)).isoformat()

    device_entry = {
        "id":            secrets.token_hex(12),
        "session_id":    session_id,
        "username":      username,
        "device_name":   get_device_name(),
        "ip_address":    get_client_ip(),
        "created_at":    datetime.utcnow().isoformat(),
        "last_seen":     datetime.utcnow().isoformat(),
        "is_active":     True,
        "trusted_until": trusted_until
    }

    devices.append(device_entry)
    devices_data["devices"] = devices
    save_devices(devices_data)
    session["device_session_id"] = session_id


def touch_current_device():
    device_session_id = session.get("device_session_id")
    if not device_session_id:
        return
    devices_data = load_devices()
    changed      = False
    for device in devices_data.get("devices", []):
        if device.get("session_id") == device_session_id and device.get("is_active", True):
            device["last_seen"] = datetime.utcnow().isoformat()
            changed = True
            break
    if changed:
        save_devices(devices_data)


def deactivate_current_device():
    device_session_id = session.get("device_session_id")
    if not device_session_id:
        return
    devices_data = load_devices()
    changed      = False
    for device in devices_data.get("devices", []):
        if device.get("session_id") == device_session_id:
            device["is_active"] = False
            device["last_seen"] = datetime.utcnow().isoformat()
            changed = True
            break
    if changed:
        save_devices(devices_data)


def get_active_devices_for_user(username):
    devices_data = load_devices()
    return [
        device for device in devices_data.get("devices", [])
        if device.get("username") == username and device.get("is_active", True)
    ]


# =========================
# AUTH HELPERS
# =========================
def is_logged_in():
    return session.get("admin_logged_in") is True


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped_view


def api_login_required():
    if not is_logged_in():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    return None


def find_admin_user(username):
    users = load_users().get("admins", [])
    return next((u for u in users if str(u.get("username", "")).strip() == username), None)


def verify_admin_credentials(username, password):
    user = find_admin_user(username)
    if not user:
        return None
    if str(user.get("password", "")).strip() != password:
        return None
    return user


def get_attempts_left():
    return session.get("attempts_left", MAX_ATTEMPTS)


def set_attempts_left(value):
    session["attempts_left"] = value


def reset_attempts_left():
    session["attempts_left"] = MAX_ATTEMPTS


def is_login_locked():
    return get_attempts_left() <= 0


def build_totp_uri(secret, username):
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="UniWise")


def make_qr_data_uri(text):
    qr      = qrcode.make(text)
    buffer  = io.BytesIO()
    qr.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


# =========================
# UPLOAD HELPERS
# =========================
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_file_type(filename):
    ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
    if ext in {"png", "jpg", "jpeg", "gif", "webp"}:
        return "image"
    if ext in {"mp4", "mov", "webm", "m4v"}:
        return "video"
    return "file"


def save_uploaded_file(file_storage):
    if not file_storage or not file_storage.filename:
        raise ValueError("No file selected.")
    original_name = secure_filename(file_storage.filename)
    if not original_name:
        raise ValueError("Invalid filename.")
    if not allowed_file(original_name):
        raise ValueError(f"File type not allowed: {original_name}")
    ext      = original_name.rsplit(".", 1)[1].lower()
    new_name = f"{uuid4().hex}.{ext}"
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], new_name)
    file_storage.save(save_path)
    return {"type": get_file_type(original_name), "url": f"/static/uploads/{new_name}", "name": original_name}


def save_multiple_uploaded_files(file_list):
    attachments = []
    for item in file_list:
        if item and item.filename:
            attachments.append(save_uploaded_file(item))
    return attachments


def gather_all_uploads_from_request():
    all_files = []
    for field_name in ["images", "videos", "files", "file"]:
        for item in request.files.getlist(field_name):
            if item and item.filename:
                all_files.append(item)
    return all_files


def delete_physical_file_by_url(file_url):
    if not file_url:
        return
    relative_path = file_url.replace("/static/", "", 1)
    full_path     = os.path.join(BASE_DIR, "static", relative_path)
    if os.path.exists(full_path):
        try:
            os.remove(full_path)
        except OSError:
            pass


def delete_post_attachments(post):
    for item in post.get("attachments", []):
        delete_physical_file_by_url(item.get("url", ""))


def detect_content_type(attachments):
    if not attachments:
        return "text"
    types = {item.get("type", "file") for item in attachments}
    if len(types) == 1:
        return list(types)[0]
    return "mixed"


# =========================
# ROUTES - PAGE VIEWS
# =========================
@app.route("/")
def index():
    if not session.get("privacy_consent_granted"):
        return redirect(url_for("privacy_consent"))
    return render_template("index.html")


@app.route("/privacy-consent")
def privacy_consent():
    return render_template("privacy-consent.html")


@app.route("/accept-consent", methods=["POST"])
def accept_consent():
    data     = request.get_json(silent=True) or {}
    read_ok  = bool(data.get("read"))
    agree_ok = bool(data.get("agree"))
    if not (read_ok and agree_ok):
        return jsonify({"success": False, "error": "Both consent options are required."}), 400
    session["privacy_consent_granted"] = True
    return jsonify({"success": True, "redirect": url_for("index")})


@app.route("/revoke-consent", methods=["POST"])
def revoke_consent():
    session.pop("privacy_consent_granted", None)
    return jsonify({"success": True})


@app.route("/resources")
def resources():
    return render_template("resources.html", resources_data=load_resources())


@app.route("/history")
def history():
    return render_template("history.html")


@app.route("/settings")
def settings():
    return render_template("settings.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if is_logged_in():
        return redirect(url_for("admin"))

    attempts_left = get_attempts_left()
    error         = ""

    if request.method == "POST":
        username        = request.form.get("username", "").strip()
        password        = request.form.get("password", "").strip()
        remember_device = request.form.get("remember_device") == "on"
        session["remember_device_requested"] = remember_device

        if is_login_locked():
            return render_template("admin-login.html", error="Maximum login attempts reached.", attempts_left=0, max_attempts=MAX_ATTEMPTS)

        found = verify_admin_credentials(username, password)
        if found:
            session["pending_admin_username"] = username
            session["admin_logged_in"]        = False
            secret = str(found.get("totp_secret", "")).strip()
            if not secret:
                return redirect(url_for("setup_2fa"))
            return redirect(url_for("admin_otp"))

        attempts_left = max(0, attempts_left - 1)
        set_attempts_left(attempts_left)
        error = "Invalid username or password."
        return render_template("admin-login.html", error=error, attempts_left=attempts_left, max_attempts=MAX_ATTEMPTS)

    return render_template(
        "admin-login.html",
        error=error,
        attempts_left=None if attempts_left == MAX_ATTEMPTS else attempts_left,
        max_attempts=MAX_ATTEMPTS
    )


@app.route("/setup-2fa", methods=["GET", "POST"])
def setup_2fa():
    pending_username = session.get("pending_admin_username")
    if not pending_username:
        return redirect(url_for("login"))
    user = find_admin_user(pending_username)
    if not user:
        return redirect(url_for("login"))

    if request.method == "POST":
        code        = request.form.get("otp", "").strip()
        temp_secret = session.get("temp_totp_secret", "")
        if not temp_secret:
            return redirect(url_for("setup_2fa"))
        totp = pyotp.TOTP(temp_secret)
        if not totp.verify(code, valid_window=1):
            uri          = build_totp_uri(temp_secret, pending_username)
            qr_code_data = make_qr_data_uri(uri)
            return render_template("admin-setup-2fa.html", error="Invalid code. Please scan the QR and try again.", secret=temp_secret, qr_code_data=qr_code_data, username=pending_username)

        users_data = load_users()
        for admin_user in users_data.get("admins", []):
            if str(admin_user.get("username", "")).strip() == pending_username:
                admin_user["totp_secret"] = temp_secret
                break
        save_users(users_data)
        session.pop("temp_totp_secret", None)
        return redirect(url_for("admin_otp"))

    existing_secret = str(user.get("totp_secret", "")).strip()
    if existing_secret:
        return redirect(url_for("admin_otp"))

    temp_secret = session.get("temp_totp_secret", "")
    if not temp_secret:
        temp_secret = pyotp.random_base32()
        session["temp_totp_secret"] = temp_secret

    uri          = build_totp_uri(temp_secret, pending_username)
    qr_code_data = make_qr_data_uri(uri)
    return render_template("admin-setup-2fa.html", error=None, secret=temp_secret, qr_code_data=qr_code_data, username=pending_username)


@app.route("/admin-otp", methods=["GET"])
def admin_otp():
    pending_username = session.get("pending_admin_username")
    if not pending_username:
        return redirect(url_for("login"))
    user = find_admin_user(pending_username)
    if not user:
        return redirect(url_for("login"))
    secret = str(user.get("totp_secret", "")).strip()
    if not secret:
        return redirect(url_for("setup_2fa"))
    return render_template("admin-otp.html", error=None, success=None)


@app.route("/verify-otp", methods=["POST"])
def verify_otp():
    pending_username = session.get("pending_admin_username")
    if not pending_username:
        return redirect(url_for("login"))
    user = find_admin_user(pending_username)
    if not user:
        return redirect(url_for("login"))
    secret = str(user.get("totp_secret", "")).strip()
    if not secret:
        return redirect(url_for("setup_2fa"))

    otp  = request.form.get("otp", "").strip()
    totp = pyotp.TOTP(secret)
    if not totp.verify(otp, valid_window=1):
        return render_template("admin-otp.html", error="Invalid OTP code.", success=None)

    session["admin_logged_in"]  = True
    session["admin_username"]   = pending_username
    register_logged_in_device(pending_username, remember_device=session.get("remember_device_requested", False))
    session.pop("pending_admin_username", None)
    session.pop("temp_totp_secret", None)
    session.pop("remember_device_requested", None)
    reset_attempts_left()
    return redirect(url_for("admin"))


@app.route("/resend-otp", methods=["GET", "POST"])
def resend_otp():
    if not session.get("pending_admin_username"):
        return redirect(url_for("login"))
    return render_template("admin-otp.html", error=None, success="Open Microsoft Authenticator and enter the latest 6-digit code. If you changed phones, ask an admin to reset 2FA.")


@app.route("/logout")
def logout():
    deactivate_current_device()
    for key in ["admin_logged_in","admin_username","pending_admin_username","attempts_left","temp_totp_secret","remember_device_requested","device_session_id"]:
        session.pop(key, None)
    return redirect(url_for("login"))


@app.route("/admin")
@login_required
def admin():
    touch_current_device()
    return render_template("admin.html", admin_name=session.get("admin_username", "Admin"))


@app.route("/admin/devices", methods=["GET"])
@login_required
def admin_devices():
    username           = session.get("admin_username", "")
    devices            = get_active_devices_for_user(username)
    current_session_id = session.get("device_session_id")
    return render_template("admin-devices.html", devices=devices, current_session_id=current_session_id)


@app.route("/admin/devices/<device_id>/revoke", methods=["POST"])
@login_required
def revoke_admin_device(device_id):
    username     = session.get("admin_username", "")
    devices_data = load_devices()
    changed      = False
    for device in devices_data.get("devices", []):
        if device.get("id") == device_id and device.get("username") == username:
            device["is_active"] = False
            device["last_seen"] = datetime.utcnow().isoformat()
            changed = True
            break
    if changed:
        save_devices(devices_data)
    return redirect(url_for("admin_devices"))


# =========================
# ROUTES - RESOURCES API
# =========================
@app.route("/api/resources", methods=["GET"])
def get_resources():
    try:
        data          = load_resources()
        data["posts"] = [serialize_post_for_admin(post) for post in data.get("posts", [])]
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/resources", methods=["POST"])
def update_resources():
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        incoming     = request.get_json() or {}
        current_data = load_resources()
        for key in ["announcement","about","contact","school","links","updates"]:
            default = [] if key == "updates" else {}
            current_data[key] = incoming.get(key, current_data.get(key, default))
        if "hero_slider" in incoming and isinstance(incoming["hero_slider"], dict):
            current_data["hero_slider"] = incoming["hero_slider"]
        if "posts" in incoming and isinstance(incoming["posts"], list):
            current_data["posts"] = [normalize_post_structure(p) for p in incoming["posts"]]
        save_resources(current_data)
        return jsonify({"success": True, "message": "Resources saved successfully."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/resources/about", methods=["POST"])
def save_about():
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        payload        = request.get_json() or {}
        about          = payload.get("about", {})
        resources_data = load_resources()
        resources_data["about"] = {
            "title": str(about.get("title", "About UniWise")).strip(),
            "text1": str(about.get("text1", "")).strip(),
            "text2": str(about.get("text2", "")).strip()
        }
        save_resources(resources_data)
        return jsonify({"success": True, "message": "About section saved successfully."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/resources/contact", methods=["POST"])
def save_contact():
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        payload        = request.get_json() or {}
        contact        = payload.get("contact", {})
        resources_data = load_resources()
        resources_data["contact"] = {
            "phone":    str(contact.get("phone", "")).strip(),
            "email":    str(contact.get("email", "")).strip(),
            "location": str(contact.get("location", "")).strip()
        }
        save_resources(resources_data)
        return jsonify({"success": True, "message": "Contact saved successfully."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/resources/hero-slider", methods=["POST"])
def save_hero_slider():
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        resources_data   = load_resources()
        current_slider   = resources_data.get("hero_slider", {"items": []})
        current_items    = current_slider.get("items", [])
        keep_existing_raw = request.form.get("keep_existing_items", "[]")
        durations_raw    = request.form.get("durations", "{}")

        try:
            keep_existing_ids = json.loads(keep_existing_raw)
            if not isinstance(keep_existing_ids, list):
                keep_existing_ids = []
        except Exception:
            keep_existing_ids = []

        try:
            durations_map = json.loads(durations_raw)
            if not isinstance(durations_map, dict):
                durations_map = {}
        except Exception:
            durations_map = {}

        kept_items = []
        for item in current_items:
            item_id = str(item.get("id", ""))
            if item_id in keep_existing_ids:
                item["duration"] = int(durations_map.get(item_id, item.get("duration", 7000)) or 7000)
                item["active"]   = True
                kept_items.append(item)
            else:
                delete_physical_file_by_url(item.get("url", ""))

        uploaded_files = []
        for field_name in ["led_media", "led_media[]", "images", "videos", "files", "file"]:
            uploaded_files.extend(request.files.getlist(field_name))

        new_items = []
        for file_storage in uploaded_files:
            if file_storage and file_storage.filename:
                saved    = save_uploaded_file(file_storage)
                item_id  = uuid4().hex
                duration = int(request.form.get(f"duration_new_{saved['name']}", 7000) or 7000)
                new_items.append({
                    "id":       item_id,
                    "type":     saved.get("type", "image"),
                    "url":      saved.get("url", ""),
                    "name":     saved.get("name", "LED Media"),
                    "duration": max(5000, duration),
                    "active":   True
                })

        resources_data["hero_slider"] = {"items": kept_items + new_items}
        save_resources(resources_data)
        return jsonify({"success": True, "message": "LED bulletin updated successfully.", "data": resources_data["hero_slider"]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =========================
# ROUTES - FAQ INSIGHTS
# =========================
@app.route("/api/faq-insights", methods=["GET"])
def get_faq_insights():
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        data      = load_faq_data()
        questions = data.get("questions", [])
        return jsonify({"success": True, "data": build_faq_insights_payload(questions)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/chatbot/faqs", methods=["GET"])
def get_chatbot_faqs():
    """
    Returns all approved FAQs for the chatbot.
    Cache-Control: no-store ensures the chatbot always gets the latest
    answers even if the admin updated them seconds ago.
    """
    try:
        data      = load_faq_data()
        questions = data.get("questions", [])
        approved  = [item for item in questions if item.get("status") == "approved"]
        approved_sorted = sorted(
            approved,
            key=lambda item: (int(item.get("count", 0)), item.get("updated_at") or item.get("created_at") or ""),
            reverse=True
        )
        response = jsonify({"success": True, "data": approved_sorted})
        # ── KEY FIX: prevent browser/proxy caching so edits are instant ──
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"]        = "no-cache"
        response.headers["Expires"]       = "0"
        return response
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/chatbot/questions", methods=["POST"])
def log_chatbot_question():
    try:
        payload    = request.get_json(silent=True) or {}
        question   = sanitize_question(payload.get("question"))
        if not question:
            return jsonify({"success": False, "error": "Question is required."}), 400
        normalized = normalize_question(question)
        if not normalized:
            return jsonify({"success": False, "error": "Question is invalid."}), 400

        data      = load_faq_data()
        questions = data.get("questions", [])
        existing  = next((item for item in questions if item.get("normalized_question") == normalized), None)
        timestamp = now_str()

        if existing:
            existing["count"]      = int(existing.get("count", 0)) + 1
            existing["updated_at"] = timestamp
            if not existing.get("question"):
                existing["question"] = question
        else:
            questions.append({
                "id":                  make_question_id(),
                "question":            question,
                "normalized_question": normalized,
                "answer":              "",
                "count":               1,
                "status":              "new",
                "source":              "chatbot",
                "created_at":          timestamp,
                "updated_at":          timestamp
            })

        save_faq_data(data)
        return jsonify({"success": True, "message": "Question logged successfully."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/sync-chat-history-to-faqs", methods=["POST"])
def sync_chat_history_to_faqs():
    try:
        payload   = request.get_json(silent=True) or {}
        questions = payload.get("questions", [])
        if not isinstance(questions, list):
            return jsonify({"success": False, "error": "Questions must be a list."}), 400

        data             = load_faq_data()
        stored_questions = data.get("questions", [])
        added_count      = 0
        updated_count    = 0
        timestamp        = now_str()

        for raw_question in questions:
            question   = sanitize_question(raw_question)
            if not question:
                continue
            normalized = normalize_question(question)
            if not normalized:
                continue
            existing = next((item for item in stored_questions if item.get("normalized_question") == normalized), None)
            if existing:
                existing["count"]      = int(existing.get("count", 0)) + 1
                existing["updated_at"] = timestamp
                if not existing.get("question"):
                    existing["question"] = question
                updated_count += 1
            else:
                stored_questions.append({
                    "id":                  make_question_id(),
                    "question":            question,
                    "normalized_question": normalized,
                    "answer":              "",
                    "count":               1,
                    "status":              "new",
                    "source":              "chatbot",
                    "created_at":          timestamp,
                    "updated_at":          timestamp
                })
                added_count += 1

        save_faq_data(data)
        return jsonify({"success": True, "message": "Chat history synced successfully.", "added": added_count, "updated": updated_count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/faq-insights", methods=["POST"])
def create_faq_manually():
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        payload    = request.get_json(silent=True) or {}
        question   = sanitize_question(payload.get("question"))
        answer     = str(payload.get("answer", "")).strip()
        approve    = bool(payload.get("approve", True))
        if not question:
            return jsonify({"success": False, "error": "Question is required."}), 400

        normalized = normalize_question(question)
        timestamp  = now_str()
        data       = load_faq_data()
        questions  = data.get("questions", [])
        existing   = next((item for item in questions if item.get("normalized_question") == normalized), None)

        if existing:
            existing["question"]   = question
            existing["answer"]     = answer
            existing["status"]     = "approved" if approve else existing.get("status", "new")
            existing["updated_at"] = timestamp
            if not existing.get("source"):
                existing["source"] = "admin"
            save_faq_data(data)
            return jsonify({"success": True, "message": "FAQ updated successfully.", "data": existing})

        new_item = {
            "id":                  make_question_id(),
            "question":            question,
            "normalized_question": normalized,
            "answer":              answer,
            "count":               0,
            "status":              "approved" if approve else "new",
            "source":              "admin",
            "created_at":          timestamp,
            "updated_at":          timestamp
        }
        questions.append(new_item)
        save_faq_data(data)
        return jsonify({"success": True, "message": "FAQ created successfully.", "data": new_item})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/faq-insights/<faq_id>", methods=["PUT"])
def update_faq_insight(faq_id):
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        payload   = request.get_json(silent=True) or {}
        question  = sanitize_question(payload.get("question"))
        answer    = str(payload.get("answer", "")).strip()
        approve   = bool(payload.get("approve", False))
        data      = load_faq_data()
        questions = data.get("questions", [])
        item      = find_question_by_id(questions, faq_id)
        if not item:
            return jsonify({"success": False, "error": "FAQ not found."}), 404
        if question:
            item["question"]            = question
            item["normalized_question"] = normalize_question(question)
        item["answer"]     = answer
        if approve:
            item["status"] = "approved"
        item["updated_at"] = now_str()
        save_faq_data(data)
        return jsonify({"success": True, "message": "FAQ updated successfully.", "data": item})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/faq-insights/<faq_id>/approve", methods=["POST"])
def approve_faq_insight(faq_id):
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        data      = load_faq_data()
        questions = data.get("questions", [])
        item      = find_question_by_id(questions, faq_id)
        if not item:
            return jsonify({"success": False, "error": "FAQ not found."}), 404
        item["status"]     = "approved"
        item["updated_at"] = now_str()
        save_faq_data(data)
        return jsonify({"success": True, "message": "FAQ approved successfully.", "data": item})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/faq-insights/<faq_id>/reset-count", methods=["POST"])
def reset_faq_count(faq_id):
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        data      = load_faq_data()
        questions = data.get("questions", [])
        item      = find_question_by_id(questions, faq_id)
        if not item:
            return jsonify({"success": False, "error": "FAQ not found."}), 404
        item["count"]      = 0
        item["updated_at"] = now_str()
        save_faq_data(data)
        return jsonify({"success": True, "message": "FAQ count reset successfully.", "data": item})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/faq-insights/<faq_id>", methods=["DELETE"])
def delete_faq_insight(faq_id):
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        data      = load_faq_data()
        questions = data.get("questions", [])
        item      = find_question_by_id(questions, faq_id)
        if not item:
            return jsonify({"success": False, "error": "FAQ not found."}), 404
        data["questions"] = [q for q in questions if str(q.get("id", "")) != str(faq_id)]
        save_faq_data(data)
        return jsonify({"success": True, "message": "FAQ deleted successfully."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/dictionary", methods=["POST"])
def api_dictionary():
    try:
        payload    = request.get_json(silent=True) or {}
        word       = str(payload.get("word", "")).strip().lower()
        if not word:
            return jsonify({"found": False, "error": "No word provided."}), 400
        dictionary = load_dictionary()
        definition = dictionary.get(word)
        if not definition:
            return jsonify({"found": False, "word": word})
        return jsonify({"found": True, "word": word, "definition": definition})
    except Exception as e:
        return jsonify({"found": False, "error": str(e)}), 500


# =========================
# ROUTES - ADMIN POSTS
# =========================
@app.route("/admin/publish", methods=["POST"])
def admin_publish():
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        post_type           = request.form.get("post_type", "announcement").strip() or "announcement"
        poster_role         = request.form.get("poster_role", "").strip() or session.get("admin_username", "Admin")
        announcement_title  = request.form.get("announcement_title", "").strip()
        announcement_body   = request.form.get("announcement_body",  "").strip()
        announcement_extra  = request.form.get("announcement_extra", "").strip()
        upload_title        = request.form.get("upload_title", "").strip()
        upload_body         = request.form.get("upload_body",  "").strip()
        all_files           = gather_all_uploads_from_request()
        attachments         = save_multiple_uploaded_files(all_files)
        resources_data      = load_resources()

        if post_type == "announcement":
            if not announcement_title and not announcement_body and not announcement_extra and not attachments:
                return jsonify({"success": False, "error": "Please write something or attach files before publishing."}), 400
            new_post = {
                "id": uuid4().hex, "type": "announcement",
                "title": announcement_title, "body": announcement_body,
                "extra": announcement_extra, "caption": announcement_body,
                "author": session.get("admin_username", "Admin"), "posted_by": poster_role,
                "content_type": detect_content_type(attachments), "attachments": attachments,
                "created_at": now_str(), "updated_at": now_str()
            }
            resources_data["announcement"] = {"title": announcement_title, "body": announcement_body, "extra": announcement_extra}
            resources_data["posts"].insert(0, new_post)

        elif post_type == "upload":
            if not upload_title and not upload_body and not attachments:
                return jsonify({"success": False, "error": "Please provide a title, body, or upload files."}), 400
            new_post = {
                "id": uuid4().hex, "type": "upload",
                "title": upload_title, "body": upload_body,
                "extra": "", "caption": upload_body,
                "author": session.get("admin_username", "Admin"), "posted_by": poster_role,
                "content_type": detect_content_type(attachments), "attachments": attachments,
                "created_at": now_str(), "updated_at": now_str()
            }
            resources_data["posts"].insert(0, new_post)
        else:
            return jsonify({"success": False, "error": "Invalid post type."}), 400

        save_resources(resources_data)
        return jsonify({"success": True, "message": "Post published successfully.", "post": serialize_post_for_admin(new_post)})
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/post", methods=["POST"])
def admin_post():
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        post_type  = request.form.get("type", "update").strip() or "update"
        title      = request.form.get("title", "").strip()
        body       = request.form.get("body",  "").strip()
        extra      = request.form.get("extra", "").strip()
        posted_by  = request.form.get("posted_by", "").strip() or session.get("admin_username", "Admin")
        file_list  = gather_all_uploads_from_request()

        if not title and not body and not extra and not any(f.filename for f in file_list):
            return jsonify({"success": False, "error": "Please write something or attach files before publishing."}), 400

        attachments    = save_multiple_uploaded_files(file_list)
        resources_data = load_resources()
        posts          = resources_data.get("posts", [])
        updates        = resources_data.get("updates", [])

        new_post = {
            "id": uuid4().hex, "type": post_type, "title": title, "body": body,
            "extra": extra, "caption": body,
            "author": session.get("admin_username", "Admin"), "posted_by": posted_by,
            "content_type": detect_content_type(attachments), "attachments": attachments,
            "created_at": now_str(), "updated_at": now_str()
        }

        posts.insert(0, new_post)
        resources_data["posts"] = posts

        if post_type == "announcement":
            resources_data["announcement"] = {
                "title": title or resources_data.get("announcement", {}).get("title", ""),
                "body":  body  or resources_data.get("announcement", {}).get("body",  ""),
                "extra": extra
            }
        elif post_type in {"status", "update"}:
            updates.insert(0, {
                "label": "Status" if post_type == "status" else "Update",
                "icon":  "bi-chat-dots-fill" if post_type == "status" else "bi-info-circle-fill",
                "title": title or "Untitled Update",
                "text":  body or extra or ""
            })
            resources_data["updates"] = updates

        save_resources(resources_data)
        return jsonify({"success": True, "message": "Post published successfully.", "post": new_post})
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/upload", methods=["POST"])
def admin_upload():
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        title      = request.form.get("title", "").strip()
        body       = request.form.get("body",  "").strip()
        posted_by  = request.form.get("posted_by", "").strip() or session.get("admin_username", "Admin")
        file_list  = gather_all_uploads_from_request()

        if not title and not body and not any(f.filename for f in file_list):
            return jsonify({"success": False, "error": "Please provide a title, body, or upload files."}), 400

        attachments    = save_multiple_uploaded_files(file_list)
        resources_data = load_resources()
        posts          = resources_data.get("posts", [])

        new_post = {
            "id": uuid4().hex, "type": "upload", "title": title, "body": body,
            "extra": "", "caption": body,
            "author": session.get("admin_username", "Admin"), "posted_by": posted_by,
            "content_type": detect_content_type(attachments), "attachments": attachments,
            "created_at": now_str(), "updated_at": now_str()
        }

        posts.insert(0, new_post)
        resources_data["posts"] = posts
        save_resources(resources_data)
        return jsonify({"success": True, "message": "Post uploaded successfully.", "post": new_post})
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/posts/<post_id>", methods=["PUT"])
def update_post(post_id):
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        title          = request.form.get("title", "").strip()
        body           = request.form.get("body",  "").strip()
        post_type      = request.form.get("type",  "upload").strip() or "upload"
        posted_by      = request.form.get("posted_by", "").strip() or session.get("admin_username", "Admin")
        resources_data = load_resources()
        posts          = resources_data.get("posts", [])
        post           = next((p for p in posts if str(p.get("id")) == str(post_id)), None)

        if not post:
            return jsonify({"success": False, "error": "Post not found."}), 404

        new_attachments = save_multiple_uploaded_files(gather_all_uploads_from_request())
        post["title"]        = title
        post["body"]         = body
        post["caption"]      = body
        post["type"]         = post_type
        post["posted_by"]    = posted_by
        post["updated_at"]   = now_str()

        if new_attachments:
            post["attachments"] = post.get("attachments", []) + new_attachments
        post["content_type"] = detect_content_type(post.get("attachments", []))
        save_resources(resources_data)
        return jsonify({"success": True, "message": "Post updated successfully.", "post": post})
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/posts/<post_id>", methods=["DELETE"])
def delete_post(post_id):
    auth_error = api_login_required()
    if auth_error:
        return auth_error
    try:
        resources_data = load_resources()
        posts          = resources_data.get("posts", [])
        target_index   = next((i for i, p in enumerate(posts) if str(p.get("id")) == str(post_id)), None)

        if target_index is None:
            return jsonify({"success": False, "error": "Post not found."}), 404

        target_post = posts.pop(target_index)
        delete_post_attachments(target_post)
        resources_data["posts"] = posts
        save_resources(resources_data)
        return jsonify({"success": True, "message": "Post deleted successfully."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =========================
# ROUTES - ADMIN FAQ INSIGHTS PAGES
# =========================
@app.route("/admin/faq-insights")
@login_required
def admin_faq_insights_page():
    touch_current_device()
    data    = load_faq_data()
    payload = build_faq_insights_payload(data.get("questions", []))
    return render_template(
        "admin-faq-insights.html",
        top_faqs=payload.get("top_faqs", []),
        new_questions=payload.get("new_questions", []),
        all_questions=payload.get("all_questions", []),
        admin_name=session.get("admin_username", "Admin")
    )


@app.route("/admin/faq-insights/<faq_id>")
@login_required
def admin_faq_insight_detail_page(faq_id):
    touch_current_device()
    data      = load_faq_data()
    questions = data.get("questions", [])
    item      = find_question_by_id(questions, faq_id)
    if not item:
        return redirect(url_for("admin_faq_insights_page"))

    normalized    = item.get("normalized_question", "")
    similar_items = []
    for q in questions:
        if str(q.get("id", "")) == str(faq_id):
            continue
        if q.get("status") == "approved":
            other_norm = q.get("normalized_question", "")
            if normalized and (normalized in other_norm or other_norm in normalized):
                similar_items.append(q)

    similar_items = sorted(
        similar_items,
        key=lambda row: (int(row.get("count", 0)), row.get("updated_at", "")),
        reverse=True
    )[:5]

    return render_template(
        "admin-faq-insight-detail.html",
        item=item, similar_items=similar_items,
        admin_name=session.get("admin_username", "Admin")
    )


@app.route("/admin/faq-insights/analytics")
@login_required
def admin_faq_insights_analytics_page():
    touch_current_device()
    data          = load_faq_data()
    questions     = data.get("questions", [])
    top_faqs      = get_top_faqs(questions, limit=20)
    new_questions = get_new_questions(questions)[:20]

    source_counts = {}
    for item in questions:
        source = item.get("source", "unknown") or "unknown"
        source_counts[source] = source_counts.get(source, 0) + 1

    source_breakdown = [
        {"source": key, "total": value}
        for key, value in sorted(source_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return render_template(
        "admin-faq-insights-analytics.html",
        top_faqs=top_faqs, new_questions=new_questions,
        source_breakdown=source_breakdown,
        admin_name=session.get("admin_username", "Admin")
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5006)