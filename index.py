import os
import sys
import secrets
import logging
import datetime
from werkzeug.utils import secure_filename
from flask import Flask, request, session, redirect, url_for, render_template, flash
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
from flask_session import Session
import uuid

# পরিবেশ ভেরিয়েবল লোড করুন
load_dotenv()

# ভার্সেলের জন্য লগিং কনফিগার করুন
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ফ্লাস্ক অ্যাপ শুরু করুন
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("VERCEL_ENV") == "production",  # প্রোডাকশনে নিরাপদ কুকিজ
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,  # ৫০ এমবি সর্বোচ্চ আপলোড সাইজ
    UPLOAD_FOLDER="/tmp",  # ভার্সেল শুধুমাত্র /tmp তে লেখার অনুমতি দেয়
    SESSION_TYPE="mongodb",  # মঙ্গোডিবিতে সেশন সংরক্ষণ
    SESSION_MONGODB_DB="mydatabase",
    SESSION_MONGODB_COLLECT="sessions"
)

# CSRF সুরক্ষা শুরু করুন
csrf = CSRFProtect(app)

# মঙ্গোডিবি সেটআপ এবং সেশন ম্যানেজমেন্ট
try:
    mongo = MongoClient(
        os.getenv("mongodb+srv://project01app01:Adnan26820027@cluster0.h8zwopc.mongodb.net"),
        serverSelectionTimeoutMS=10000,  # সংযোগের জন্য ১০ সেকেন্ড টাইমআউট
        maxPoolSize=10  # সার্ভারলেসের জন্য অপ্টিমাইজ
    )
    mongo.server_info()  # সংযোগ পরীক্ষা করুন
    logger.info("মঙ্গোডিবি সংযোগ স্থাপিত")
    app.config["SESSION_MONGODB"] = mongo
    db = mongo["mydatabase"]
    users_col = db["users"]
    proj_col = db["projects"]
    Session(app)  # সেশন ম্যানেজমেন্ট শুরু করুন
except ConnectionFailure as e:
    logger.error(f"মঙ্গোডিবি সংযোগ ব্যর্থ: {e}")
    raise Exception("মঙ্গোডিবি সংযোগে ব্যর্থ। MONGO_URI পরীক্ষা করুন।")

# আপলোড ফোল্ডার নিশ্চিত করুন
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# ফাইল আপলোডের জন্য ধ্রুবক
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif"}
PHONE_RE = re.compile(r'^\+?[0-9]{11,15}$')

# নিরাপদ ফর্ম হ্যান্ডলিংয়ের জন্য WTForms
class LoginForm(FlaskForm):
    phone = StringField("ফোন নম্বর", validators=[DataRequired(), Regexp(PHONE_RE, message="অবৈধ ফোন নম্বর")])
    password = PasswordField("পাসওয়ার্ড", validators=[DataRequired(), Length(min=6)])
    submit = SubmitField("লগইন করুন")

class RegisterForm(FlaskForm):
    name = StringField("নাম", validators=[DataRequired(), Length(min=2, max=100)])
    phone = StringField("ফোন নম্বর", validators=[DataRequired(), Regexp(PHONE_RE, message="অবৈধ ফোন নম্বর")])
    password = PasswordField("পাসওয়ার্ড", validators=[DataRequired(), Length(min=6)])
    submit = SubmitField("নিবন্ধন করুন")

class ProjectForm(FlaskForm):
    name = StringField("প্রকল্পের নাম", validators=[DataRequired(), Length(min=2, max=100)])
    type = SelectField("প্রাণীর ধরন", choices=[("goat", "ছাগল"), ("cow", "গরু")], validators=[DataRequired()])
    purchase_date = DateField("ক্রয়ের তারিখ", validators=[DataRequired()])
    weight = FloatField("ওজন (কেজি)", validators=[DataRequired()])
    submit = SubmitField("প্রকল্প তৈরি করুন")

class WeightForm(FlaskForm):
    weight = FloatField("ওজন (কেজি)", validators=[DataRequired()])
    submit = SubmitField("ওজন আপডেট করুন")

class PhotoForm(FlaskForm):
    photos = FileField("ছবি আপলোড করুন", validators=[DataRequired()])
    phase = SelectField("পর্যায়", choices=[("morning", "সকাল"), ("midday", "দুপুর"), ("afternoon", "বিকেল"), ("evening", "সন্ধ্যা")], validators=[DataRequired()])
    submit = SubmitField("ছবি আপলোড করুন")

# সহায়ক ফাংশন
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
                    {"description": "শেড পরিষ্কার করুন, ট্রফ ধুয়ে ফেলুন, গরুর পা হাঁটু পর্যন্ত ধুয়ে ফেলুন", "time_range": "6:00 AM - 7:00 AM"},
                    {"description": f"সবুজ ঘাস খাওয়ান ({grass(weight, animal)} কেজি)", "time_range": "7:00 AM - 8:00 AM"},
                    {"description": f"{feed_level(weight, animal)} কেজি শস্য + গুড় জল (5g/5L) খাওয়ান", "time_range": "8:00 AM - 9:00 AM"},
                    {"description": "খড় খাওয়ান (খড়ের উপর গুড় জল ছিটিয়ে দিন)", "time_range": "9:00 AM - 10:00 AM"},
                    {"description": "প্রয়োজন অনুযায়ী সবুজ ঘাস সরবরাহ করুন", "time_range": "10:00 AM - 11:00 AM"},
                ]
            },
            {
                "phase": "midday",
                "tasks": [
                    {"description": "ট্রফ জল দিয়ে ধুয়ে ফেলুন, শেড পরিষ্কার করুন", "time_range": "11:00 AM - 12:00 PM"},
                    {"description": "গরুকে গোসল করান (গ্রীষ্মে প্রতিদিন, শীতে প্রতি দ্বিতীয় দিন)", "time_range": "12:00 PM - 1:00 PM"},
                    {"description": "ট্রফে পরিষ্কার জল সরবরাহ করুন এবং গরুকে বিশ্রাম দিন", "time_range": "1:00 PM - 3:00 PM"},
                ]
            },
            {
                "phase": "afternoon",
                "tasks": [
                    {"description": f"সবুজ ঘাস খাওয়ান ({grass(weight, animal)} কেজি)", "time_range": "3:00 PM - 4:00 PM"},
                    {"description": f"{feed_level(weight, animal)} কেজি শস্য খাওয়ান", "time_range": "4:00 PM - 5:00 PM"},
                    {"description": "খড় খাওয়ান (খড়ের উপর গুড় জল ছিটিয়ে দিন)", "time_range": "5:00 PM - 6:00 PM"},
                    {"description": "প্রয়োজন অনুযায়ী সবুজ ঘাস সরবরাহ করুন", "time_range": "6:00 PM - 6:45 PM"},
                ]
            },
            {
                "phase": "evening",
                "tasks": [
                    {"description": "শেড পরিষ্কার করুন, রাতের জন্য মশার ধূপ জ্বালান, ট্রফ পরিষ্কার করুন এবং জল সরবরাহ করুন", "time_range": "7:00 PM - 8:00 PM"}
                ]
            }
        ]
    elif animal == "goat":
        return [
            {
                "phase": "morning",
                "tasks": [
                    {"description": "ছাগলের শেড পরিষ্কার করুন, ট্রফ ধুয়ে ফেলুন, ছাগলের পা হাঁটু পর্যন্ত ধুয়ে ফেলুন", "time_range": "6:00 AM - 7:00 AM"},
                    {"description": f"সবুজ ঘাস খাওয়ান ({grass(weight, animal)} কেজি)", "time_range": "7:00 AM - 8:00 AM"},
                    {"description": f"{feed_level(weight, animal)} গ্রাম শস্য (বাটিতে মেপে) + গুড় জল (5g/5L) খাওয়ান", "time_range": "8:00 AM - 9:00 AM"},
                    {"description": "খড় খাওয়ান (খড়ের উপর গুড় জল ছিটিয়ে দিন)", "time_range": "9:00 AM - 10:00 AM"},
                    {"description": "প্রয়োজন অনুযায়ী সবুজ ঘাস সরবরাহ করুন", "time_range": "10:00 AM - 11:00 AM"},
                    {"description": "ট্রফ জল দিয়ে ধুয়ে ফেলুন, শেড পরিষ্কার করুন", "time_range": "11:00 AM - 12:00 PM"},
                ]
            },
            {
                "phase": "midday",
                "tasks": [
                    {"description": "ট্রফে পরিষ্কার জল সরবরাহ করুন এবং ছাগলকে বিশ্রাম দিন", "time_range": "1:00 PM - 3:00 PM"},
                    {"description": f"সবুজ ঘাস খাওয়ান ({grass(weight, animal)} কেজি)", "time_range": "3:00 PM - 4:00 PM"},
                    {"description": f"{feed_level(weight, animal)} গ্রাম শস্য খাওয়ান", "time_range": "4:00 PM - 5:00 PM"},
                    {"description": "খড় খাওয়ান (খড়ের উপর গুড় জল ছিটিয়ে দিন)", "time_range": "5:00 PM - 6:00 PM"},
                    {"description": "প্রয়োজন অনুযায়ী সবুজ ঘাস সরবরাহ করুন", "time_range": "6:00 PM - 6:45 PM"},
                ]
            },
            {
                "phase": "evening",
                "tasks": [
                    {"description": "শেড পরিষ্কার করুন, রাতের জন্য মশার ধূপ জ্বালান, ট্রফ পরিষ্কার করুন এবং জল সরবরাহ করুন", "time_range": "7:00 PM - 8:00 PM"},
                ]
            }
        ]
    else:
        return [
            {
                "phase": "default",
                "tasks": [
                    {"description": f"{animal} এর জন্য সাধারণ কাজ", "time_range": "–"}
                ]
            }
        ]

# সেশন চেক করার জন্য মিডলওয়্যার
@app.before_request
def check_session():
    if "user_id" in session:
        session.permanent = True
        session.modified = True

# রুটস
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        phone = form.phone.data.strip()
        pwd = form.password.data
        user = users_col.find_one({"phone": phone})

        if user and bcrypt.checkpw(pwd.encode(), user["password"]):
            session["user_id"] = str(user["_id"])
            session.permanent = True
            if user.get("role") == "admin":
                session["admin"] = True
                flash("স্বাগতম, অ্যাডমিন!", "success")
                return redirect(url_for("admin_dashboard"))
            flash("স্বাগতম!", "success")
            return redirect(url_for("projects"))
        flash("অবৈধ ফোন নম্বর বা পাসওয়ার্ড!", "danger")
    return render_template("login.html", form=form)

@app.route("/register", methods=["GET", "POST"])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        phone = form.phone.data.strip()

        if users_col.find_one({"phone": phone}):
            flash("ফোন নম্বর ইতিমধ্যে ব্যবহৃত!", "warning")
            return redirect(url_for("register"))

        pw_hash = bcrypt.hashpw(form.password.data.encode(), bcrypt.gensalt())
        user_id = users_col.insert_one({"name": name, "phone": phone, "password": pw_hash}).inserted_id
        session["user_id"] = str(user_id)
        session.permanent = True
        flash("অ্যাকাউন্ট তৈরি হয়েছে!", "success")
        return redirect(url_for("projects"))
    return render_template("register.html", form=form)

@app.route("/admin/dashboard", methods=["GET", "POST"])
def admin_dashboard():
    if not session.get("admin"):
        flash("অ্যাডমিন অ্যাক্সেস প্রয়োজন!", "danger")
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
    flash("অ্যাডমিন লগআউট হয়েছে।", "info")
    return redirect(url_for("login"))

@app.route("/admin/users")
def admin_users():
    if not session.get("admin"):
        flash("অ্যাডমিন অ্যাক্সেস প্রয়োজন!", "danger")
        return redirect(url_for("login"))

    users = list(users_col.find({}, {"password": 0}))
    return render_template("admin_users.html", users=users)

@app.route("/admin/user/<uid>")
def admin_user_detail(uid):
    if not session.get("admin"):
        flash("অ্যাডমিন অ্যাক্সেস প্রয়োজন!", "danger")
        return redirect(url_for("login"))

    try:
        user = users_col.find_one({"_id": ObjectId(uid)})
        if not user:
            flash("ব্যবহারকারী পাওয়া যায়নি", "danger")
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
        logger.error(f"অ্যাডমিন_ইউজার_ডিটেইল এ ত্রুটি: {e}")
        flash("একটি ত্রুটি ঘটেছে", "danger")
        return redirect(url_for("admin_users"))

@app.route("/logout")
def logout():
    session.clear()
    flash("লগআউট হয়েছে!", "info")
    return redirect(url_for("login"))

@app.route("/wait")
def wait():
    return render_template("wait.html")

@app.route("/projects")
def projects():
    if "user_id" not in session:
        flash("অনুগ্রহ করে লগইন করুন!", "warning")
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
        flash("অনুগ্রহ করে লগইন করুন!", "warning")
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
            flash("প্রকল্প তৈরি হয়েছে!", "success")
            return redirect(url_for("projects"))
        except Exception as e:
            logger.error(f"প্রকল্প তৈরিতে ত্রুটি: {e}")
            flash("প্রকল্প তৈরি করতে ব্যর্থ", "danger")
    return render_template("new_project.html", form=form)

@app.route("/projects/<pid>/dashboard")
def dashboard(pid):
    if "user_id" not in session:
        flash("অনুগ্রহ করে লগইন করুন!", "warning")
        return redirect(url_for("login"))

    try:
        proj = proj_col.find_one({"_id": ObjectId(pid), "owner": session["user_id"]})
        if not proj:
            flash("প্রকল্প পাওয়া যায়নি!", "danger")
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
        logger.error(f"ড্যাশবোর্ডে ত্রুটি: {e}")
        flash("একটি ত্রুটি ঘটেছে", "danger")
        return redirect(url_for("projects"))

@app.route("/projects/<pid>/delete", methods=["POST"])
@csrf.exempt
def delete_project(pid):
    if "user_id" not in session:
        flash("অনুগ্রহ করে লগইন করুন!", "warning")
        return redirect(url_for("login"))

    try:
        proj = proj_col.find_one({"_id": ObjectId(pid), "owner": session["user_id"]})
        if not proj:
            flash("প্রকল্প পাওয়া যায়নি!", "danger")
            return redirect(url_for("projects"))

        for task_idx, photos in proj.get("task_photo", {}).items():
            for phase, photo_list in photos.items():
                for photo in photo_list:
                    photo_path = os.path.join(app.config["UPLOAD_FOLDER"], photo)
                    if os.path.exists(photo_path):
                        try:
                            os.remove(photo_path)
                        except Exception as e:
                            logger.error(f"ছবি মুছে ফেলতে ত্রুটি {photo_path}: {e}")

        proj_col.delete_one({"_id": ObjectId(pid)})
        flash("প্রকল্প এবং সম্পর্কিত ছবি মুছে ফেলা হয়েছে!", "success")
        return redirect(url_for("projects"))
    except Exception as e:
        logger.error(f"প্রকল্প মুছে ফেলতে ত্রুটি: {e}")
        flash("প্রকল্প মুছে ফেলতে ব্যর্থ", "danger")
        return redirect(url_for("projects"))

@app.route("/projects/<pid>/weight", methods=["POST"])
def update_weight(pid):
    if "user_id" not in session:
        flash("অনুগ্রহ করে লগইন করুন!", "warning")
        return redirect(url_for("login"))

    form = WeightForm()
    if form.validate_on_submit():
        try:
            weight = form.weight.data
            proj = proj_col.find_one({"_id": ObjectId(pid), "owner": session["user_id"]})
            if not proj:
                flash("প্রকল্প পাওয়া যায়নি!", "danger")
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
            flash("ওজন এবং খাদ্য স্তর আপডেট হয়েছে!", "success")
            return redirect(url_for("dashboard", pid=pid))
        except Exception as e:
            logger.error(f"ওজন আপডেট করতে ত্রুটি: {e}")
            flash("ওজন আপডেট করতে ব্যর্থ", "danger")
    return redirect(url_for("dashboard", pid=pid))

@app.route("/projects/<pid>/tasks/save", methods=["POST"])
def save_tasks(pid):
    if "user_id" not in session:
        flash("অনুগ্রহ করে লগইন করুন!", "warning")
        return redirect(url_for("login"))

    try:
        proj = proj_col.find_one({"_id": ObjectId(pid), "owner": session["user_id"]})
        if not proj:
            flash("প্রকল্প পাওয়া যায়নি!", "danger")
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
        flash("কাজগুলি আপডেট হয়েছে!", "success")
        return redirect(url_for("dashboard", pid=pid))
    except Exception as e:
        logger.error(f"কাজ সংরক্ষণে ত্রুটি: {e}")
        flash("কাজ সংরক্ষণে ব্যর্থ", "danger")
        return redirect(url_for("dashboard", pid=pid))

@app.route("/projects/<pid>/photos/upload", methods=["POST"])
def upload_photos(pid):
    if "user_id" not in session:
        flash("অনুগ্রহ করে লগইন করুন!", "warning")
        return redirect(url_for("login"))

    form = PhotoForm()
    if form.validate_on_submit():
        try:
            proj = proj_col.find_one({"_id": ObjectId(pid), "owner": session["user_id"]})
            if not proj:
                flash("প্রকল্প পাওয়া যায়নি!", "danger")
                return redirect(url_for("projects"))

            phase = form.phase.data
            files = form.photos.data
            today = date.today().isoformat()
            today_photos = proj.get("task_photo", {}).get(today, {})
            phase_photos = today_photos.get(phase, [])

            saved = []
            for file in files:
                if file and allowed_file(file.filename):
                    filename = f"{uuid.uuid4()}_{secure_filename(file.filename)}"
                    file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                    file.save(file_path)
                    saved.append(filename)
                else:
                    flash(f"অবৈধ ফাইল বাদ দেওয়া হয়েছে: {file.filename}", "warning")

            phase_photos.extend(saved)
            proj_col.update_one(
                {"_id": proj["_id"]},
                {"$set": {f"task_photo.{today}.{phase}": phase_photos}}
            )
            flash(f"{len(saved)}টি ছবি {phase} এ {today} তারিখে আপলোড হয়েছে!", "success")
            return redirect(url_for("dashboard", pid=pid))
        except Exception as e:
            logger.error(f"ছবি আপলোডে ত্রুটি: {e}")
            flash("ছবি আপলোডে ব্যর্থ", "danger")
    return redirect(url_for("dashboard", pid=pid))

# ত্রুটি হ্যান্ডলার
@app.errorhandler(404)
def not_found(e):
    logger.warning(f"404 ত্রুটি: {request.url}")
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f"500 ত্রুটি: {e}")
    return render_template("500.html"), 500

# ভার্সেল সার্ভারলেস হ্যান্ডলার
def handler(event, context):
    from serverless_wsgi import handle_request
    return handle_request(app, event, context)

if __name__ == "__main__":
    logger.info("http://localhost:5000 এ ডেভেলপমেন্ট সার্ভার শুরু হচ্ছে")
    app.run(host="0.0.0.0", port=5000, debug=os.getenv("VERCEL_ENV") != "production")
