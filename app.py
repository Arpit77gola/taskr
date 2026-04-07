import os
from flask import Flask, render_template, request, redirect, session, jsonify
from flask_mysqldb import MySQL
import bcrypt
from datetime import datetime, time

app = Flask(__name__)
@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
app.secret_key = "secret123"

app.config['MYSQL_HOST']     = os.environ.get('MYSQL_HOST', 'localhost')
app.config['MYSQL_USER']     = os.environ.get('MYSQL_USER', 'root')
app.config['MYSQL_PASSWORD'] = os.environ.get('MYSQL_PASSWORD', '')
app.config['MYSQL_DB']       = os.environ.get('MYSQL_DB', 'task_app')
app.config['MYSQL_PORT']     = int(os.environ.get('MYSQL_PORT', 3306))

app.config['MYSQL_SSL_CA'] = '/etc/ssl/certs/ca-certificates.crt'
app.config['MYSQL_SSL']      = {'ssl': {'ssl-mode': 'required'}}
mysql = MySQL(app)


# ──────────────────────────────────────────
# 🧮 POINTS CALCULATORS
# ──────────────────────────────────────────

def calc_wakeup_points(submit_time):
    start    = time(6, 0)
    deadline = time(6, 30)

    if submit_time > deadline:
        return -5

    total_mins  = 30  # 6:00 to 6:30
    passed_mins = (submit_time.hour * 60 + submit_time.minute) - (start.hour * 60 + start.minute)

    ratio  = passed_mins / total_mins
    points = round(10 - ratio * 5)   # 10 → 5
    return max(5, min(10, points))


def calc_workout_points(minutes):
    if minutes <= 0:
        return 0
    points = round((minutes / 90) * 30)
    return min(30, max(1, points))


def calc_steps_points(steps):
    if steps < 8000:
        return 0
    extra = ((steps - 8000) // 1000) * 2
    return min(30, 10 + extra)


def calc_water_points(litres):
    if litres <= 0:
        return 0
    points = round((litres / 4) * 30)
    return min(30, max(1, points))


# ──────────────────────────────────────────
# 🔐 LOGIN
# ──────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        session.clear()
        username = request.form['username']
        password = request.form['password']

        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()
        cur.close()

        if not user:
            return render_template("login.html", error="Username not found ❌")

        if not bcrypt.checkpw(password.encode('utf-8'), user[2].encode('utf-8')):
            return render_template("login.html", error="Wrong password ❌")

        session['user_id'] = user[0]
        session['username'] = user[1]
        return redirect("/dashboard")

    return render_template("login.html")


# ──────────────────────────────────────────
# 🏠 DASHBOARD
# ──────────────────────────────────────────

@app.route("/dashboard")
def dashboard():
    if 'user_id' not in session:
        return redirect("/")

    cur   = mysql.connection.cursor()
    today = datetime.now().date()

    cur.execute("SELECT * FROM tasks")
    tasks = cur.fetchall()

    cur.execute("""
        SELECT SUM(points) FROM submissions 
        WHERE user_id=%s AND date=%s
    """, (session['user_id'], today))
    total = cur.fetchone()[0] or 0

    cur.execute("""
        SELECT users.username, SUM(submissions.points)
        FROM submissions
        JOIN users ON submissions.user_id = users.id
        WHERE date=%s
        GROUP BY users.username
        ORDER BY SUM(submissions.points) DESC
    """, (today,))
    comparison = cur.fetchall()

    cur.execute("""
        SELECT SUM(points) FROM submissions
        WHERE user_id=%s 
        AND YEARWEEK(date, 1) = YEARWEEK(CURDATE(), 1)
    """, (session['user_id'],))
    weekly_points = cur.fetchone()[0] or 0

    cur.close()

    return render_template(
        "dashboard.html",
        tasks=tasks,
        username=session['username'],
        total=total,
        comparison=comparison,
        weekly_points=weekly_points
    )


# ──────────────────────────────────────────
# 📊 DASHBOARD DATA (AJAX)
# ──────────────────────────────────────────

@app.route("/dashboard_data")
def dashboard_data():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    cur   = mysql.connection.cursor()
    today = datetime.now().date()

    cur.execute("""
        SELECT SUM(points) FROM submissions 
        WHERE user_id=%s AND date=%s
    """, (session['user_id'], today))
    total = cur.fetchone()[0] or 0

    cur.execute("""
        SELECT SUM(points) FROM submissions
        WHERE user_id=%s 
        AND YEARWEEK(date, 1) = YEARWEEK(CURDATE(), 1)
    """, (session['user_id'],))
    weekly = cur.fetchone()[0] or 0

    cur.execute("""
        SELECT users.username, SUM(submissions.points)
        FROM submissions
        JOIN users ON submissions.user_id = users.id
        WHERE date=%s
        GROUP BY users.username
        ORDER BY SUM(submissions.points) DESC
    """, (today,))
    comparison = cur.fetchall()

    cur.close()

    return jsonify({
        "total": total,
        "weekly": weekly,
        "comparison": [[row[0], row[1]] for row in comparison]
    })


# ──────────────────────────────────────────
# 📋 TASK SUBMIT (AJAX)
# tasks columns: [0]id [1]title [2]description [3]deadline_time [4]task_type
# ──────────────────────────────────────────

@app.route("/task/<int:task_id>", methods=["POST"])
def task_submit(task_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    cur   = mysql.connection.cursor()
    now   = datetime.now()
    today = now.date()

    cur.execute("SELECT * FROM tasks WHERE id=%s", (task_id,))
    task = cur.fetchone()

    if not task:
        cur.close()
        return jsonify({"error": "Task not found"}), 404

    task_type = task[4]

    # ── Deadline check (for wakeup — using task[3])
    deadline = datetime.strptime(str(task[3]), "%H:%M:%S").time()

    # ── Duplicate check
    cur.execute("""
        SELECT * FROM submissions 
        WHERE user_id=%s AND task_id=%s AND date=%s
    """, (session['user_id'], task_id, today))

    if cur.fetchone():
        cur.close()
        return jsonify({"error": "Already submitted today ❌"}), 400

    # ── Points calculate
    points      = 0
    extra_value = 0

    if task_type == 'wakeup':
        points = calc_wakeup_points(now.time())

    elif task_type == 'workout':
        minutes     = float(request.form.get('workout_minutes', 0))
        extra_value = minutes
        # Deadline miss hogi toh points nahi
        if now.time() > deadline:
            points = 0
        else:
            points = calc_workout_points(minutes)

    elif task_type == 'steps':
        steps       = int(request.form.get('steps', 0))
        extra_value = steps
        points      = calc_steps_points(steps)

    elif task_type == 'water':
        litres      = float(request.form.get('water_litres', 0))
        extra_value = litres
        points      = calc_water_points(litres)
    elif task_type == 'learning':
        hours       = float(request.form.get('workout_minutes', 0))  # hours aata hai ab
        minutes     = hours * 60
        extra_value = minutes
        points      = calc_workout_points(minutes)  # same logic

    elif task_type == 'talking':
        minutes     = float(request.form.get('workout_minutes', 0))
        extra_value = minutes
        points      = calc_workout_points(minutes)

    # ── Insert submission
    cur.execute("""
        INSERT INTO submissions 
        (user_id, task_id, report_text, submitted_at, points, date, extra_value)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        session['user_id'],
        task_id,
        '',
        now,
        points,
        today,
        extra_value
    ))

    mysql.connection.commit()
    cur.close()

    return jsonify({"success": True, "points": points})


# ──────────────────────────────────────────
# 🔄 AUTO PENALTY — Wakeup miss check
# ──────────────────────────────────────────

@app.route("/check_penalties")
def check_penalties():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    now   = datetime.now()
    today = now.date()

    if now.time() < time(6, 30):
        return jsonify({"applied": False})

    cur = mysql.connection.cursor()

    cur.execute("SELECT id FROM tasks WHERE task_type='wakeup'")
    wakeup = cur.fetchone()

    if not wakeup:
        cur.close()
        return jsonify({"applied": False})

    wakeup_id = wakeup[0]

    cur.execute("""
        SELECT * FROM submissions 
        WHERE user_id=%s AND task_id=%s AND date=%s
    """, (session['user_id'], wakeup_id, today))

    if cur.fetchone():
        cur.close()
        return jsonify({"applied": False})

    cur.execute("""
        INSERT INTO submissions 
        (user_id, task_id, report_text, submitted_at, points, date, extra_value)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (session['user_id'], wakeup_id, 'auto_penalty', now, -5, today, 0))

    mysql.connection.commit()
    cur.close()

    return jsonify({"applied": True, "message": "Wake up penalty applied -5 ⏰"})


# ──────────────────────────────────────────
# 📦 TASK PARTIAL (AJAX)
# ──────────────────────────────────────────

@app.route("/task_data/<int:task_id>")
def task_data(task_id):
    if 'user_id' not in session:
        return "Unauthorized", 401

    cur   = mysql.connection.cursor()
    today = datetime.now().date()

    cur.execute("SELECT * FROM tasks WHERE id=%s", (task_id,))
    task = cur.fetchone()

    # ✅ FIX 4: Already submitted check
    cur.execute("""
        SELECT points FROM submissions 
        WHERE user_id=%s AND task_id=%s AND date=%s
    """, (session['user_id'], task_id, today))
    submission = cur.fetchone()
    cur.close()

    return render_template("task_partial.html", task=task, already_submitted=submission)


# ──────────────────────────────────────────
# 🔑 RESET PASSWORD
# ──────────────────────────────────────────

@app.route("/reset", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        username = request.form['username']
        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()

        if not user:
            cur.close()
            return render_template("reset.html", error="Username doesn't exist ❌")

        new_password = request.form.get('new_password')

        if new_password:
            hashed = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
            cur.execute(
                "UPDATE users SET password=%s WHERE username=%s",
                (hashed.decode('utf-8'), username)
            )
            mysql.connection.commit()
            cur.close()
            return render_template("reset.html", success="Password updated ✅")

        cur.close()
        return render_template("reset.html", username=username)

    return render_template("reset.html")

@app.route("/leaderboard_data")
def leaderboard_data():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    cur   = mysql.connection.cursor()
    today = datetime.now().date()

    # ── Daily per user per task
    cur.execute("""
        SELECT users.username, tasks.title, SUM(submissions.points)
        FROM submissions
        JOIN users ON submissions.user_id = users.id
        JOIN tasks ON submissions.task_id = tasks.id
        WHERE submissions.date = %s
        GROUP BY users.username, tasks.title
    """, (today,))
    daily_rows = cur.fetchall()

    # ── Weekly per user per task
    cur.execute("""
        SELECT users.username, tasks.title, SUM(submissions.points)
        FROM submissions
        JOIN users ON submissions.user_id = users.id
        JOIN tasks ON submissions.task_id = tasks.id
        WHERE YEARWEEK(submissions.date, 1) = YEARWEEK(CURDATE(), 1)
        GROUP BY users.username, tasks.title
    """)
    weekly_rows = cur.fetchall()

    # ── Overall per user
    cur.execute("""
        SELECT users.username, SUM(submissions.points)
        FROM submissions
        JOIN users ON submissions.user_id = users.id
        GROUP BY users.username
        ORDER BY SUM(submissions.points) DESC
    """)
    overall_rows = cur.fetchall()

    cur.close()

    def build(rows):
        data = {}
        for username, task, pts in rows:
            if username not in data:
                data[username] = {}
            data[username][task] = int(pts or 0)
        return data

    return jsonify({
        "daily":   build(daily_rows),
        "weekly":  build(weekly_rows),
        "overall": [[r[0], int(r[1] or 0)] for r in overall_rows]
    })


# ──────────────────────────────────────────
# 🚪 LOGOUT
# ──────────────────────────────────────────

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))