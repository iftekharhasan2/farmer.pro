import os
import sys
import secrets
import logging
import datetime
from werkzeug.utils import secure_filename
from flask import Flask, request, session, redirect, url_for, render_template, flash, abort
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, PasswordField, SubmitField, FloatField, SelectField, FileField, DateField
from wtforms.validators import DataRequired, Length, Regexp
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from bson.objectid import ObjectId
import bcrypt
import re
from dotenv import load_dotenv
from datetime import date, timedelta
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('app.log')
    ]
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production",  # Secure cookies in production
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),  # Sessions last 7 days
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,  # 50 MB max upload size
    UPLOAD_FOLDER=os.path.join(os.getcwd(), "static", "uploads")
)

# Initialize CSRF protection
csrf = CSRFProtect(app)

# Initialize rate limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# Ensure upload folder exists
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# MongoDB setup
try:
    mongo = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"), serverSelectionTimeoutMS=5000)
    mongo.server_info()  # Test connection
    db = mongo["mydatabase"]
    users_col = db["users"]
    proj_col = db["projects"]
except ConnectionFailure as e:
    logger.error(f"MongoDB connection failed: {e}")
    sys.exit(1)

# File upload constants
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif"}
PHONE_RE = re.compile(r'^\+?[0-9]{11,15}$')

# WTForms for secure form handling
class LoginForm(FlaskForm):
    phone = StringField("Phone", validators=[DataRequired(), Regexp(PHONE_RE, message="Invalid phone number")])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=6)])
    submit = SubmitField("Login")

class RegisterForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(min=2, max=100)])
    phone = StringField("Phone", validators=[DataRequired(), Regexp(PHONE_RE, message="Invalid phone number")])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=6)])
    submit = SubmitField("Register")

class ProjectForm(FlaskForm):
    name = StringField("Project Name", validators=[DataRequired(), Length(min=2, max=100)])
    type = SelectField("Animal Type", choices=[("goat", "Goat"), ("cow", "Cow")], validators=[DataRequired()])
    purchase_date = DateField("Purchase Date", validators=[DataRequired()])
    weight = FloatField("Weight (kg)", validators=[DataRequired()])
    submit = SubmitField("Create Project")

class WeightForm(FlaskForm):
    weight = FloatField("Weight (kg)", validators=[DataRequired()])
    submit = SubmitField("Update Weight")

class PhotoForm(FlaskForm):
    photos = FileField("Upload Photos", validators=[DataRequired()])
    phase = SelectField("Phase", choices=[("morning", "Morning"), ("midday", "Midday"), ("afternoon", "Afternoon"), ("evening", "Evening")], validators=[DataRequired()])
    submit = SubmitField("Upload Photos")

# Helper functions
def valid_phone(phone):
    return PHONE_RE.fullmatch(phone) is not None

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def days_since(d):
    if isinstance(d, str):
        d = datetime.datetime.fromisoformat(d).date()
    elif isinstance(d, datetime.datetime):
        d = d.date()
    return (date.today() - d).days + 1

def feed_level(weight, animal):
    if animal == "goat":
        if weight < 10:
            return 100
        elif weight <= 15:
            return 150
        elif weight < 20:
            return 200
        return 200
    else:  # cow
        if weight < 150:
            return 1
        elif weight < 280:
            return 2
        return 3

def grass(weight, animal):
    if animal == "goat":
        return 2.5
    else:  # cow
        if weight < 150:
            return 5
        elif weight < 250:
            return 7.5
        elif weight < 400:
            return 12.5
        return 17.5

def build_schedule(day, weight, animal):
    if animal == "cow":
        return [
            {
                "phase": "morning",
                "tasks": [
                    {"description": "Clean the shed, wash the trough, wash cow's legs up to knees", "time_range": "6:00 AM - 7:00 AM"},
                    {"description": f"Feed green grass ({grass(weight, animal)} kg)", "time_range": "7:00 AM - 8:00 AM"},
                    {"description": f"Feed {feed_level(weight, animal)} kg of grain + molasses water (5g/5L)", "time_range": "8:00 AM - 9:00 AM"},
                    {"description": "Feed straw (sprinkle molasses water on straw)", "time_range": "9:00 AM - 10:00 AM"},
                    {"description": "Provide green grass as needed", "time_range": "10:00 AM - 11:00 AM"},
                ]
            },
            {
                "phase": "midday",
                "tasks": [
                    {"description": "Wash trough with water, clean the shed", "time_range": "11:00 AM - 12:00 PM"},
                    {"description": "Bathe the cow (daily in summer, every other day in winter)", "time_range": "12:00 PM - 1:00 PM"},
                    {"description": "Provide clean water in trough and let the cow rest", "time_range": "1:00 PM - 3:00 PM"},
                ]
            },
            {
                "phase": "afternoon",
                "tasks": [
                    {"description": f"Feed green grass ({grass(weight, animal)} kg)", "time_range": "3:00 PM - 4:00 PM"},
                    {"description": f"Feed {feed_level(weight, animal)} kg of grain", "time_range": "4:00 PM - 5:00 PM"},
                    {"description": "Feed straw (sprinkle molasses water on straw)", "time_range": "5:00 PM - 6:00 PM"},
                    {"description": "Provide green grass as needed", "time_range": "6:00 PM - 6:45 PM"},
                ]
            },
            {
                "phase": "evening",
                "tasks": [
                    {"description": "Clean the shed, light a mosquito coil for the night, clean trough and provide water", "time_range": "7:00 PM - 8:00 PM"}
                ]
            }
        ]
    elif animal == "goat":
        return [
            {
                "phase": "morning",
                "tasks": [
                    {"description": "Clean the goat shed, wash the trough, wash goat's legs up to knees", "time_range": "6:00 AM - 7:00 AM"},
                    {"description": f"Feed green grass ({grass(weight, animal)} kg)", "time_range": "7:00 AM - 8:00 AM"},
                    {"description": f"Feed {feed_level(weight, animal)} g of grain (measure in a bowl) + molasses water (5g/5L)", "time_range": "8:00 AM - 9:00 AM"},
                    {"description": "Feed straw (sprinkle molasses water on straw)", "time_range": "9:00 AM - 10:00 AM"},
                    {"description": "Provide green grass as needed", "time_range": "10:00 AM - 11:00 AM"},
                    {"description": "Wash trough with water, clean the shed", "time_range": "11:00 AM - 12:00 PM"},
                ]
            },
            {
                "phase": "midday",
                "tasks": [
                    {"description": "Provide clean water in trough and let the goat rest", "time_range": "1:00 PM - 3:00 PM"},
                    {"description": f"Feed green grass ({grass(weight, animal)} kg)", "time_range": "3:00 PM - 4:00 PM"},
                    {"description": f"Feed {feed_level(weight, animal)} g of grain", "time_range": "4:00 PM - 5:00 PM"},
                    {"description": "Feed straw (sprinkle molasses water on straw)", "time_range": "5:00 PM - 6:00 PM"},
                    {"description": "Provide green grass as needed", "time_range": "6:00 PM - 6:45 PM"},
                ]
            },
            {
                "phase": "evening",
                "tasks": [
                    {"description": "Clean the shed, light a mosquito coil for the night, clean trough and provide water", "time_range": "7:00 PM - 8:00 PM"},
                ]
            }
        ]
    else:
        return [
            {
                "phase": "default",
                "tasks": [
                    {"description": f"General tasks for {animal}", "time_range": "–"}
                ]
            }
        ]

# Middleware to check session
@app.before_request
def check_session():
    if "user_id" in session:
        session.permanent = True  # Make session persistent
        session.modified = True  # Mark session as modified to ensure it saves

# Routes
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")  # Prevent brute-force login attempts
def login():
    form = LoginForm()
    if form.validate_on_submit():
        phone = form.phone.data.strip()
        pwd = form.password.data
        user = users_col.find_one({"phone": phone})

        if user and bcrypt.checkpw(pwd.encode(), user["password"]):
            session["user_id"] = str(user["_id"])
            session.permanent = True  # Ensure session persists
            if user.get("role") == "admin":
                session["admin"] = True
                flash("Welcome, Admin!", "success")
                return redirect(url_for("admin_dashboard"))
            flash("Welcome!", "success")
            return redirect(url_for("projects"))
        flash("Invalid phone or password!", "danger")
    return render_template("login.html", form=form)

@app.route("/register", methods=["GET", "POST"])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        phone = form.phone.data.strip()

        if users_col.find_one({"phone": phone}):
            flash("Phone number already used!", "warning")
            return redirect(url_for("register"))

        pw_hash = bcrypt.hashpw(form.password.data.encode(), bcrypt.gensalt())
        user_id = users_col.insert_one({"name": name, "phone": phone, "password": pw_hash}).inserted_id
        session["user_id"] = str(user_id)
        session.permanent = True
        flash("Account created!", "success")
        return redirect(url_for("projects"))
    return render_template("register.html", form=form)

@app.route("/admin/dashboard", methods=["GET", "POST"])
def admin_dashboard():
    if not session.get("admin"):
        flash("Admin access required!", "danger")
        return redirect(url_for("login"))

    projects = list(proj_col.find())
    for p in projects:
        p["days"] = days_since(p["purchase_date"])
        p["schedule"] = build_schedule(p["days"], p["weight"], p["type"])
    return render_template(
        "admin02.html",
        projects=projects,
        today=date.today().isoformat()
    )

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    session.pop("user_id", None)
    flash("Admin logged out.", "info")
    return redirect(url_for("login"))

@app.route("/admin/users")
def admin_users():
    if not session.get("admin"):
        flash("Admin access required!", "danger")
        return redirect(url_for("login"))

    users = list(users_col.find({}, {"password": 0}))
    return render_template("admin_users.html", users=users)

@app.route("/admin/user/<uid>")
def admin_user_detail(uid):
    if not session.get("admin"):
        flash("Admin access required!", "danger")
        return redirect(url_for("login"))

    try:
        user = users_col.find_one({"_id": ObjectId(uid)})
        if not user:
            flash("User not found", "danger")
            return redirect(url_for("admin_users"))

        projects = list(proj_col.find({"owner": uid}))
        for p in projects:
            p["days"] = days_since(p["purchase_date"])
            p["schedule"] = build_schedule(p["days"], p["weight"], p["type"])
        return render_template(
            "admin_user_detail.html",
            user=user,
            projects=projects,
            today=date.today().isoformat()
        )
    except Exception as e:
        logger.error(f"Error in admin_user_detail: {e}")
        flash("An error occurred", "danger")
        return redirect(url_for("admin_users"))

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out!", "info")
    return redirect(url_for("login"))

@app.route("/wait")
def wait():
    return render_template("wait.html")

@app.route("/projects")
def projects():
    if "user_id" not in session:
        flash("Please log in!", "warning")
        return redirect(url_for("login"))

    projs = list(proj_col.find({"owner": session["user_id"]}))
    days_map = {str(p["_id"]): days_since(p["purchase_date"]) for p in projs}
    return render_template(
        "projects.html",
        projects=projs,
        days=days_map,
        str=str
    )

@app.route("/projects/new", methods=["GET", "POST"])
def new_project():
    if "user_id" not in session:
        flash("Please log in!", "warning")
        return redirect(url_for("login"))

    form = ProjectForm()
    if form.validate_on_submit():
        try:
            doc = {
                "owner": session["user_id"],
                "name": form.name.data.strip(),
                "type": form.type.data,
                "purchase_date": form.purchase_date.data.isoformat(),
                "weight": form.weight.data,
                "feed_level": feed_level(form.weight.data, form.type.data),
                "target": 24 if form.type.data == "goat" else 350,
                "check_period": 30,
                "task_done": {},
                "task_photo": {},
            }
            proj_col.insert_one(doc)
            flash("Project created!", "success")
            return redirect(url_for("projects"))
        except Exception as e:
            logger.error(f"Error creating project: {e}")
            flash("Failed to create project", "danger")
    return render_template("new_project.html", form=form)

@app.route("/projects/<pid>/dashboard")
def dashboard(pid):
    if "user_id" not in session:
        flash("Please log in!", "warning")
        return redirect(url_for("login"))

    try:
        proj = proj_col.find_one({"_id": ObjectId(pid), "owner": session["user_id"]})
        if not proj:
            flash("Project not found!", "danger")
            return redirect(url_for("projects"))

        days = days_since(proj["purchase_date"])
        period = proj["check_period"]
        show_weight = (days % period == 0 and days != 0)
        days_left = (period - (days % period)) % period

        if days % period == 0 and days != 0 and proj.get("last_check") != days:
            new_level = feed_level(
                proj["weight"] + (30 if proj["type"] == "cow" else 0),
                proj["type"]
            )
            proj_col.update_one(
                {"_id": proj["_id"]},
                {"$set": {"feed_level": new_level, "last_check": days}}
            )
            proj["feed_level"] = new_level
            proj["last_check"] = days

        schedule = build_schedule(days, proj["weight"], proj["type"])
        today = date.today().isoformat()
        today_done = proj.get("task_done", {}).get(today, {})
        today_photos = proj.get("task_photo", {}).get(today, {})

        if "task_done" not in proj:
            proj["task_done"] = {}
        if "task_photo" not in proj:
            proj["task_photo"] = {}

        return render_template(
            "dashboard02.html",
            project=proj,
            schedule=schedule,
            days=days,
            today_done=today_done,
            today_photos=today_photos,
            today=today,
            show_weight_input=show_weight,
            days_left=days_left,
            photo_form=PhotoForm(),
            weight_form=WeightForm()
        )
    except Exception as e:
        logger.error(f"Error in dashboard: {e}")
        flash("An error occurred", "danger")
        return redirect(url_for("projects"))

@app.route("/projects/<pid>/delete", methods=["POST"])
@csrf.exempt  # Exempt CSRF for simplicity, but consider adding CSRF token in production
def delete_project(pid):
    if "user_id" not in session:
        flash("Please log in!", "warning")
        return redirect(url_for("login"))

    try:
        proj = proj_col.find_one({"_id": ObjectId(pid), "owner": session["user_id"]})
        if not proj:
            flash("Project not found!", "danger")
            return redirect(url_for("projects"))

        for task_idx, photos in proj.get("task_photo", {}).items():
            for phase, photo_list in photos.items():
                for photo in photo_list:
                    photo_path = os.path.join(app.config["UPLOAD_FOLDER"], photo)
                    if os.path.exists(photo_path):
                        try:
                            os.remove(photo_path)
                        except Exception as e:
                            logger.error(f"Error deleting photo {photo_path}: {e}")

        proj_col.delete_one({"_id": ObjectId(pid)})
        flash("Project and associated photos deleted!", "success")
        return redirect(url_for("projects"))
    except Exception as e:
        logger.error(f"Error deleting project: {e}")
        flash("Failed to delete project", "danger")
        return redirect(url_for("projects"))

@app.route("/projects/<pid>/weight", methods=["POST"])
def update_weight(pid):
    if "user_id" not in session:
        flash("Please log in!", "warning")
        return redirect(url_for("login"))

    form = WeightForm()
    if form.validate_on_submit():
        try:
            weight = form.weight.data
            proj = proj_col.find_one({"_id": ObjectId(pid), "owner": session["user_id"]})
            if not proj:
                flash("Project not found!", "danger")
                return redirect(url_for("projects"))

            proj_col.update_one(
                {"_id": ObjectId(pid), "owner": session["user_id"]},
                {"$set": {"weight": weight}}
            )
            new_level = feed_level(weight, proj["type"])
            proj_col.update_one(
                {"_id": ObjectId(pid), "owner": session["user_id"]},
                {"$set": {"feed_level": new_level}}
            )
            flash("Weight & feed level updated!", "success")
            return redirect(url_for("dashboard", pid=pid))
        except Exception as e:
            logger.error(f"Error updating weight: {e}")
            flash("Failed to update weight", "danger")
    return redirect(url_for("dashboard", pid=pid))

@app.route("/projects/<pid>/tasks/save", methods=["POST"])
def save_tasks(pid):
    if "user_id" not in session:
        flash("Please log in!", "warning")
        return redirect(url_for("login"))

    try:
        proj = proj_col.find_one({"_id": ObjectId(pid), "owner": session["user_id"]})
        if not proj:
            flash("Project not found!", "danger")
            return redirect(url_for("projects"))

        schedule = build_schedule(days_since(proj["purchase_date"]), proj.get("weight", 0), proj["type"])
        today = date.today().isoformat()
        done_dict = {}
        for phase_dict in schedule:
            phase = phase_dict["phase"]
            for i, _ in enumerate(phase_dict["tasks"]):
                key = f"{phase}.{i}"
                done_dict[key] = (request.form.get(f"done_{key}") == "yes")

        proj_col.update_one(
            {"_id": proj["_id"]},
            {"$set": {f"task_done.{today}": done_dict}}
        )
        flash("Tasks updated!", "success")
        return redirect(url_for("dashboard", pid=pid))
    except Exception as e:
        logger.error(f"Error saving tasks: {e}")
        flash("Failed to save tasks", "danger")
        return redirect(url_for("dashboard", pid=pid))

@app.route("/projects/<pid>/photos/upload", methods=["POST"])
def upload_photos(pid):
    if "user_id" not in session:
        flash("Please log in!", "warning")
        return redirect(url_for("login"))

    form = PhotoForm()
    if form.validate_on_submit():
        try:
            proj = proj_col.find_one({"_id": ObjectId(pid), "owner": session["user_id"]})
            if not proj:
                flash("Project not found!", "danger")
                return redirect(url_for("projects"))

            phase = form.phase.data
            files = form.photos.data
            today = date.today().isoformat()
            today_photos = proj.get("task_photo", {}).get(today, {})
            phase_photos = today_photos.get(phase, [])

            saved = []
            for file in files:
                if file and allowed_file(file.filename):
                    filename = f"{ObjectId()}_{secure_filename(file.filename)}"
                    file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                    saved.append(filename)
                else:
                    flash(f"Invalid file skipped: {file.filename}", "warning")

            phase_photos.extend(saved)
            proj_col.update_one(
                {"_id": proj["_id"]},
                {"$set": {f"task_photo.{today}.{phase}": phase_photos}}
            )
            flash(f"Uploaded {len(saved)} photo(s) to {phase} on {today}!", "success")
            return redirect(url_for("dashboard", pid=pid))
        except Exception as e:
            logger.error(f"Error uploading photos: {e}")
            flash("Failed to upload photos", "danger")
    return redirect(url_for("dashboard", pid=pid))

# Error handlers
@app.errorhandler(404)
def not_found(e):
    logger.warning(f"404 error: {request.url}")
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f"500 error: {e}")
    return render_template("500.html"), 500

# Shutdown handler
def shutdown(signum, frame):
    logger.info("Shutting down server...")
    mongo.close()
    sys.exit(0)

if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGINT, shutdown)
    logger.info("Starting development server on http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=os.getenv("FLASK_ENV") != "production")
