import os
import secrets
import logging
import datetime
import signal
import sys
from werkzeug.utils import secure_filename
from flask import Flask, request, session, redirect, url_for, render_template, flash
from pymongo import MongoClient
from bson.objectid import ObjectId
import bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# Rate Limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["100 per hour"]
)

# MongoDB
mongo = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
db = mongo["mydatabase"]
users_col = db["users"]
proj_col = db["projects"]

# File Upload Config
UPLOAD_FOLDER = os.path.join(os.getcwd(), "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif"}

def allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def days_since(d):
    if isinstance(d, str):
        d = datetime.datetime.fromisoformat(d).date()
    elif isinstance(d, datetime.datetime):
        d = d.date()
    return (datetime.date.today() - d).days

def feed_level(weight, animal):
    if animal == "goat":
        return 1 if weight < 15 else 2 if weight < 18 else 3 if weight < 21 else 4 if weight < 23 else 5
    return 1 if weight < 150 else 2 if weight < 280 else 3

def Grass(weight, animal):
    if animal == "goat":
        return 1 if weight < 15 else 2 if weight < 18 else 3 if weight < 21 else 4 if weight < 23 else 5
    return 5 if weight < 150 else 7.5 if weight < 250 else 12.5 if weight < 400 else 17.5

def build_schedule(day, weight, animal):
    # Your existing schedule generation logic should be placed here
    return []  # Replace with actual implementation

@app.route("/")
def index():
    return redirect(url_for("projects") if "user_id" in session else url_for("login"))

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        user = user_col.find_one({"email": email})
        if user and bcrypt.checkpw(password.encode("utf-8"), user["password"]):
            session["user_id"] = str(user["_id"])
            flash("Login successful!", "success")
            return redirect(url_for("profile"))
        flash("Invalid credentials", "danger")
        return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        if users_col.find_one({"email": email}):
            flash("Already registered!", "warning")
            return redirect(url_for("register"))
        pw_hash = bcrypt.hashpw(request.form["password"].encode(), bcrypt.gensalt())
        users_col.insert_one({"name": name, "email": email, "password": pw_hash})
        flash("Account created!", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out!", "info")
    return redirect(url_for("login"))

@app.route("/projects")
def projects():
    if "user_id" not in session:
        return redirect(url_for("login"))
    projs = list(proj_col.find({"owner": session["user_id"]}))
    days_map = {str(p["_id"]): days_since(p["purchase_date"]) for p in projs}
    return render_template("projects.html", projects=projs, days=days_map, str=str)

@app.route("/projects/new", methods=["GET", "POST"])
def new_project():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if request.method == "POST":
        doc = {
            "owner": session["user_id"],
            "name": request.form["name"].strip(),
            "type": request.form["type"],
            "purchase_date": request.form["purchase_date"],
            "weight": float(request.form["weight"]),
            "feed_level": feed_level(float(request.form["weight"]), request.form["type"]),
            "target": 24 if request.form["type"] == "goat" else 350,
            "check_period": 30 if request.form["type"] == "cow" else 1,
            "task_done": {},
            "task_photo": {},
        }
        proj_col.insert_one(doc)
        flash("Project created!", "success")
        return redirect(url_for("projects"))
    return render_template("new_project.html")

@app.route("/projects/<pid>/photos/upload", methods=["POST"])
@limiter.limit("5 per minute")
def upload_photos(pid):
    if "user_id" not in session:
        return redirect(url_for("login"))
    proj = proj_col.find_one({"_id": ObjectId(pid), "owner": session["user_id"]})
    if not proj:
        flash("Project not found!", "danger")
        return redirect(url_for("projects"))

    phase = request.form.get("phase")
    if not phase:
        flash("Phase not specified.", "warning")
        return redirect(url_for("dashboard", pid=pid))

    files = request.files.getlist("photos")
    if not files or all(f.filename == '' for f in files):
        flash("No photos selected.", "warning")
        return redirect(url_for("dashboard", pid=pid))

    phase_photos = proj.get("task_photo", {}).get(phase, [])
    if isinstance(phase_photos, str):
        phase_photos = [phase_photos]

    saved = []
    for file in files:
        if file and allowed(file.filename):
            filename = f"{ObjectId()}_{secure_filename(file.filename)}"
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            saved.append(filename)
        else:
            flash(f"Invalid file skipped: {file.filename}", "warning")

    phase_photos.extend(saved)
    proj_col.update_one({"_id": proj["_id"]}, {"$set": {f"task_photo.{phase}": phase_photos}})
    flash(f"Uploaded {len(saved)} photo(s) to phase '{phase}'!", "success")
    return redirect(url_for("dashboard", pid=pid))

# Shutdown

def shutdown(signum, frame):
    logging.info("Shutting down …")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown)
    logging.info("Starting app on http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
