from flask import Flask, render_template, request, redirect, url_for, send_file, flash
import psycopg2
import os
from datetime import date, datetime
import io
import openpyxl
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

app = Flask(__name__)
app.secret_key = "supersecretkey"  # Needed for flash messages

# -----------------------------
# Database Connection
# -----------------------------
def get_db():
    return psycopg2.connect(os.environ["DATABASE_URL"])

def init_db():
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS subjects (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS attendance (
                    id SERIAL PRIMARY KEY,
                    student_id INTEGER REFERENCES students(id),
                    subject_id INTEGER REFERENCES subjects(id),
                    date DATE,
                    status TEXT
                )
            """)
        conn.commit()

# -----------------------------
# Template Helpers
# -----------------------------
@app.context_processor
def inject_now():
    """Make `now` available in templates as a function."""
    return {'now': lambda: datetime.utcnow()}

# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/add_student", methods=["GET", "POST"])
def add_student():
    if request.method == "POST":
        name = request.form["name"].strip()
        if name:
            with get_db() as conn:
                with conn.cursor() as c:
                    c.execute("INSERT INTO students (name) VALUES (%s)", (name,))
                conn.commit()
            flash(f"✅ Student '{name}' added successfully!", "success")
        return redirect(url_for("index"))
    return render_template("add_student.html")

@app.route("/add_subject", methods=["GET", "POST"])
def add_subject():
    if request.method == "POST":
        name = request.form["name"].strip()
        if name:
            with get_db() as conn:
                with conn.cursor() as c:
                    c.execute("INSERT INTO subjects (name) VALUES (%s)", (name,))
                conn.commit()
            flash(f"✅ Subject '{name}' added successfully!", "success")
        return redirect(url_for("index"))
    return render_template("add_subject.html")

@app.route("/mark_attendance", methods=["GET", "POST"])
def mark_attendance():
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT * FROM students ORDER BY name ASC")
            students = c.fetchall()
            c.execute("SELECT * FROM subjects ORDER BY name ASC")
            subjects = c.fetchall()

    if request.method == "POST":
        subject_id = request.form["subject"]
        the_date = request.form.get("date") or str(date.today())

        for student in students:
            status = request.form.get(f"student_{student[0]}", "Absent")
            with get_db() as conn:
                with conn.cursor() as c:
                    # Prevent duplicate attendance
                    c.execute("""
                        SELECT id FROM attendance
                        WHERE student_id=%s AND subject_id=%s AND date=%s
                    """, (student[0], subject_id, the_date))
                    if not c.fetchone():
                        c.execute("""
                            INSERT INTO attendance (student_id, subject_id, date, status)
                            VALUES (%s, %s, %s, %s)
                        """, (student[0], subject_id, the_date, status))
                        conn.commit()

        flash("✅ Attendance marked successfully!", "success")
        return redirect(url_for("report"))

    return render_template("mark_attendance.html", students=students, subjects=subjects, today=str(date.today()))

@app.route("/report", methods=["GET"])
def report():
    selected_subject = request.args.get("subject")
    selected_date = request.args.get("date")

    with get_db() as conn:
        with conn.cursor() as c:
            # Fetch subjects
            c.execute("SELECT * FROM subjects ORDER BY name ASC")
            subjects = c.fetchall()

            # Build attendance query
            query = """
                SELECT students.id, students.name, subjects.name, attendance.date, attendance.status
                FROM attendance
                JOIN students ON attendance.student_id = students.id
                JOIN subjects ON attendance.subject_id = subjects.id
                WHERE 1=1
            """
            params = []
            if selected_subject and selected_subject != "all":
                query += " AND subjects.id = %s"
                params.append(selected_subject)
            if selected_date:
                query += " AND attendance.date = %s"
                params.append(selected_date)
            query += " ORDER BY attendance.date DESC, students.name ASC"
            c.execute(query, tuple(params))
            records = c.fetchall()

            # Calculate percentage per student
            c.execute("""
                SELECT students.id, students.name,
                    COUNT(attendance.id) AS total,
                    SUM(CASE WHEN status='Present' THEN 1 ELSE 0 END) AS present
                FROM students
                LEFT JOIN attendance ON attendance.student_id = students.id
                GROUP BY students.id, students.name
                ORDER BY students.name ASC
            """)
            stats = c.fetchall()

    percentages = []
    for s in stats:
        total = s[2]
        present = s[3] or 0
        percent = (present / total * 100) if total > 0 else 0
        percentages.append((s[1], total, present, round(percent, 2)))

    return render_template(
        "report.html",
        records=records,
        subjects=subjects,
        selected_subject=selected_subject,
        selected_date=selected_date,
        percentages=percentages
    )

# -----------------------------
# Export Routes
# -----------------------------
@app.route("/export/excel")
def export_excel():
    start = request.args.get("start")
    end = request.args.get("end")

    with get_db() as conn:
        with conn.cursor() as c:
            query = """
                SELECT students.name, subjects.name, attendance.date, attendance.status
                FROM attendance
                JOIN students ON attendance.student_id = students.id
                JOIN subjects ON attendance.subject_id = subjects.id
                WHERE 1=1
            """
            params = []
            if start:
                query += " AND attendance.date >= %s"
                params.append(start)
            if end:
                query += " AND attendance.date <= %s"
                params.append(end)
            c.execute(query, tuple(params))
            rows = c.fetchall()

    # Build Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Student", "Subject", "Date", "Status"])
    for row in rows:
        ws.append(row)

    file_stream = io.BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)

    return send_file(file_stream, as_attachment=True, download_name="attendance.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/export/pdf")
def export_pdf():
    start = request.args.get("start")
    end = request.args.get("end")

    with get_db() as conn:
        with conn.cursor() as c:
            query = """
                SELECT students.name, subjects.name, attendance.date, attendance.status
                FROM attendance
                JOIN students ON attendance.student_id = students.id
                JOIN subjects ON attendance.subject_id = subjects.id
                WHERE 1=1
            """
            params = []
            if start:
                query += " AND attendance.date >= %s"
                params.append(start)
            if end:
                query += " AND attendance.date <= %s"
                params.append(end)
            c.execute(query, tuple(params))
            rows = c.fetchall()

    # Build PDF
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    y = 750
    p.setFont("Helvetica", 12)
    p.drawString(50, y, "Attendance Report")
    y -= 30
    for row in rows:
        line = f"{row[0]} | {row[1]} | {row[2]} | {row[3]}"
        p.drawString(50, y, line)
        y -= 20
        if y < 50:
            p.showPage()
            y = 750
    p.save()
    buffer.seek(0)

    return send_file(buffer, as_attachment=True, download_name="attendance.pdf", mimetype="application/pdf")

# -----------------------------
# Initialize DB and run app
# -----------------------------
if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
