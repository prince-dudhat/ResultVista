from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
)
from flask_bcrypt import Bcrypt
from database import db, User
from authlib.integrations.flask_client import OAuth
import re
import os
import pandas as pd
from werkzeug.utils import secure_filename
import uuid
from mail import init_mail, send_welcome_email, send_delete_account_email
from cleanup_old_files import cleanup_old_files
from dotenv import load_dotenv
from flask_mail import Message
from mail import mail, init_mail
from flask_session import Session

cleanup_old_files()  # Clean up old files on server startup
load_dotenv()  # Load environment variables from .env file

app = Flask(__name__)

# Configure server-side session storage
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = "./flask_session"
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_USE_SIGNER"] = True
app.config["SESSION_COOKIE_SECURE"] = False

Session(app)  # Initialize the session extension

# app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///user.db"
app.config["SQLALCHEMY_DATABASE_URI"] = "mysql+pymysql://root:@localhost:3306/user"
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
bcrypt = Bcrypt(app)

app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER")

# Initialize mail with Flask app
init_mail(app)

app.config["UPLOAD_FOLDER"] = "uploads"
app.config["ALLOWED_EXTENSIONS"] = {"xlsx", "xls"}

# Initialize database
db.init_app(app)

with app.app_context():
    db.create_all()

oauth = OAuth(app)

google = oauth.register(
    name="google",
    client_id="558733018158-utoo4fupr8h2eb5tm763g3i3r0kq15sv.apps.googleusercontent.com",
    client_secret="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",
    access_token_url="https://oauth2.googleapis.com/token",
    authorize_url="https://accounts.google.com/o/oauth2/auth",
    api_base_url="https://www.googleapis.com/oauth2/v2/",
    client_kwargs={"scope": "email profile"},
)


# Home route
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


# Google OAuth registration route
@app.route("/google-register")
def google_register():
    redirect_uri = url_for("auth_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


# Google OAuth callback route
@app.route("/auth/callback")
def auth_callback():
    token = google.authorize_access_token()
    resp = google.get("userinfo", token=token)
    user_info = resp.json()

    password = user_info["id"]
    name = user_info["name"]
    email = user_info.get("email")
    picture = user_info.get("picture")

    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        session["user_id"] = existing_user.id
        session["login_success"] = True
    else:
        hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")
        user = User(
            password=hashed_password,
            name=name,
            email=email,
            picture=picture,
        )
        db.session.add(user)
        db.session.commit()
        send_welcome_email(email, name)
        session["user_id"] = user.id

    return redirect(url_for("dashboard"))


# Registration route
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("mail")
        password = request.form.get("password")
        ConPassword = request.form.get("ConPassword")

        # Store form data to pass back on error
        form_data = {"name": name, "email": email}

        # check if user already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            return render_template(
                "register.html",
                error="User with this email already exists",
                **form_data,
            )

        # validate email format
        pat = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(pat, email):
            return render_template(
                "register.html",
                error="Invalid email format",
                **form_data,
            )

        # validate password strength
        password_regex = re.compile(
            r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$"
        )

        if not password_regex.match(password):
            return render_template(
                "register.html",
                error="Password must be at least 8 characters long and include uppercase, lowercase, number, and special character",
                **form_data,
            )

        # check if passwords match
        if password != ConPassword:
            return render_template(
                "register.html", error="Passwords do not match", **form_data
            )

        # if all validations pass, create new user
        hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")
        new_user = User(name=name, email=email, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        send_welcome_email(email, name)
        return redirect(url_for("login"))

    return render_template("register.html")


# Login route
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("mail")
        password = request.form.get("password")

        # Store form data
        form_data = {"email": email}

        user = User.query.filter_by(email=email).first()

        # validate user credentials
        if user and bcrypt.check_password_hash(user.password, password):
            session["user_id"] = user.id
            session["login_success"] = True
            return redirect(url_for("dashboard"))
        else:  # invalid credentials
            return render_template(
                "login.html", error="Invalid email or password", **form_data
            )

    return render_template("login.html")


@app.after_request
def add_cache_control(response):

    # Add headers to prevent caching
    if "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Create uploads directory if it doesn't exist
if not os.path.exists(app.config["UPLOAD_FOLDER"]):
    os.makedirs(app.config["UPLOAD_FOLDER"])


# Check allowed file extensions
def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]
    )


# Validate Excel structure
def validate_excel_structure(df):
    """Validate the structure of the uploaded Excel file"""
    errors = []

    # Check required columns
    required_columns = ["sr", "enrollment", "name"]

    # Convert column names to lowercase for comparison
    actual_columns = [str(col).strip().lower() for col in df.columns]

    # Check for required columns
    for req_col in required_columns:
        if req_col not in str(actual_columns):
            errors.append(f"Missing required column: '{req_col}'")

    # Check for at least one marks column
    marks_columns = [col for col in actual_columns if "marks" in col]
    if not marks_columns:
        errors.append(
            "No marks/subject columns found. Add at least one subject column."
        )

    # Check for valid data type for 'sr' column
    sr_columns = [col for col in df.columns if "sr" in str(col).lower()]
    for col in sr_columns:
        if not pd.api.types.is_integer_dtype(df[col]):
            errors.append("'sr.no' column should contain numeric values")

    # Check for valid data type for 'enrollment number'
    enrollment_columns = [col for col in df.columns if "enrollment" in str(col).lower()]
    for col in enrollment_columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            errors.append("'enrollment' column should contain numeric values")

    # Check for only alphabetic characters and space in name column
    name_columns = [col for col in df.columns if "name" in str(col).lower()]
    name_pattern = re.compile(r"^[A-Za-z\s]+$")
    for col in name_columns:
        for name in df[col]:
            if not isinstance(name, str) or not name_pattern.match(name):
                errors.append(
                    "'name' column should contain only alphabetic characters with a single spaces"
                )

    # Check for valid data type for 'marks'
    marks_columns = [col for col in df.columns if "marks" in col.lower()]
    for col in marks_columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            errors.append(f"'{col}' column should contain numeric values")

    # Check for extra columns
    allowed_columns = len(required_columns) + len(marks_columns)
    if len(actual_columns) > allowed_columns:
        errors.append(
            f"There are {len(actual_columns) - allowed_columns} extra columns found."
        )

    # Check if any marks exceed max_marks
    max_marks = request.form.get("total_marks")
    if max_marks == "custom":
        max_marks = int(request.form.get("custom_marks"))
    else:
        max_marks = int(max_marks)

    for col in marks_columns:
        if df[col].max() > max_marks:
            errors.append(
                f"Values in '{col}' exceed the maximum allowed marks of {max_marks}"
            )

    if errors:
        return False, errors

    return True, []


# Dashboard route
@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    # Check if user is logged in
    if "user_id" not in session:
        return redirect(url_for("login"))

    # Fetch user details
    user = User.query.get(session["user_id"])

    # If user not found, clear session and redirect to login
    if not user:
        session.clear()
        return redirect(url_for("login"))

    upload_error = None
    upload_success = None
    preview_data = None
    stats = None

    if request.method == "POST":
        # Check if the post request has the file part
        if "excel_file" not in request.files:
            upload_error = "No file selected"
        else:
            file = request.files["excel_file"]
            max_marks = 0

            # Get max_marks from form
            if request.form.get("total_marks"):
                if request.form.get("total_marks") == "custom":
                    max_marks = int(request.form.get("custom_marks"))
                else:
                    max_marks = int(request.form.get("total_marks"))

        # Store max_marks in session for later use
        session["max_marks"] = max_marks

        # If user does not select file, browser submits empty file
        if file.filename == "":
            upload_error = "No file selected"
        elif file and allowed_file(file.filename):
            # Convert filename to simple name
            filename = secure_filename(file.filename)
            # Get 32-char rendom hexadecimal string with simple file name
            unique_filename = f"{uuid.uuid4().hex}_{filename}"
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], unique_filename)

            try:
                # Save file temporarily
                file.save(filepath)

                file_size_mb = round(os.path.getsize(filepath) / (1024 * 1024), 2)

                # Read Excel file
                if filename.endswith(".xlsx"):
                    df = pd.read_excel(filepath, engine="openpyxl")
                else:
                    df = pd.read_excel(filepath)

                # Validate structure
                is_valid, errors = validate_excel_structure(df)

                if is_valid:
                    stats = {
                        "total_students": len(df),
                        "total_columns": len(df.columns),
                        "subjects": [
                            col for col in df.columns if "marks" in str(col).lower()
                        ],
                        "file_name": filename,
                        "file_size": file_size_mb,
                    }

                    # Get preview data (first 5 rows)
                    preview_data = df.head().to_dict("records")

                    # Store file info in session for processing
                    session["uploaded_file"] = filepath
                    session["file_stats"] = stats
                    session["preview_data"] = preview_data

                    # Clear previous processed results
                    session.pop("processed_results", None)
                    session.pop("subjects_list", None)

                    upload_success = f"File '{filename}' uploaded successfully! Validated {len(df)} student records."
                else:
                    upload_error = "Validation errors: " + ", ".join(errors)

                # Clean up temporary file if not keeping it
                if not is_valid:
                    if os.path.exists(filepath):
                        os.remove(filepath)

            except Exception as e:
                print(f"Error processing file: {str(e)}")
                # Clean up if file was saved
                if "filepath" in locals() and os.path.exists(filepath):
                    os.remove(filepath)
                return render_template(
                    "dashboard.html",
                    user=user,
                )
        else:
            upload_error = (
                "Invalid file type. Please upload Excel files only (.xlsx, .xls)"
            )

    return render_template(
        "dashboard.html",
        user=user,
        upload_error=upload_error,
        upload_success=upload_success,
        preview_data=preview_data,
        stats=stats,
        login_success=session.pop("login_success", None),
    )


# Results processing route
@app.route("/process-results", methods=["GET", "POST"])
def process_results():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user = User.query.get(session["user_id"])
    if not user:
        session.clear()
        return redirect(url_for("login"))

    # Check if file was uploaded
    if "uploaded_file" not in session:
        return redirect(url_for("dashboard"))

    filepath = session.get("uploaded_file")
    max_marks = session.get("max_marks")

    try:
        # Read the uploaded file
        if filepath.endswith(".xlsx"):
            df = pd.read_excel(filepath, engine="openpyxl")
        else:
            df = pd.read_excel(filepath)

        # Process results
        results = []
        subjects = []

        # Identify subject columns (columns containing "marks")
        for col in df.columns:
            if "marks" in str(col).lower():
                subjects.append(str(col))

        enrollment_col = None
        for col in df.columns:
            if "enrollment" in str(col).lower():
                enrollment_col = col
                break

        name = None
        for col in df.columns:
            if "name" in str(col).lower():
                name = col
                break

        # Calculate results for each student
        for _, row in df.iterrows():

            student_data = {
                "enrollment_no": int(row.get(enrollment_col)),
                "name": row.get(name),
                "subjects": {},
                "total": 0,
                "percentage": 0,
                "grade": "F",
                "status": "FAIL",
            }

            # Process each subject
            subject_total = 0
            for subject in subjects:
                marks = float(row[subject])
                student_data["subjects"][subject] = marks
                subject_total += marks

            # Calculate total and percentage
            student_data["total"] = round(subject_total, 2)
            student_data["percentage"] = round(
                (subject_total / (len(subjects) * max_marks)) * 100, 2
            )

            # Determine grade
            percentage = student_data["percentage"]
            if percentage >= 90:
                student_data["grade"] = "A"
                student_data["status"] = "PASS"
            elif percentage >= 75:
                student_data["grade"] = "B"
                student_data["status"] = "PASS"
            elif percentage >= 60:
                student_data["grade"] = "C"
                student_data["status"] = "PASS"
            elif percentage >= 33:
                student_data["grade"] = "D"
                student_data["status"] = "PASS"
            else:
                student_data["grade"] = "F"
                student_data["status"] = "FAIL"

            results.append(student_data)

        # Sort students by percentage (descending) to calculate rank
        results.sort(key=lambda x: x["percentage"], reverse=True)

        # Add rank to each student
        for i, student in enumerate(results, start=1):
            student["rank"] = i

        # Calculate summary statistics
        total_students = len(results)
        pass_count = sum(1 for s in results if s["status"] == "PASS")
        fail_count = total_students - pass_count
        max_total = max([s["total"] for s in results])

        summary = {
            "total_students": total_students,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "max_total": max_total,
        }

        session["processed_results"] = results  # Store processed results in session
        session["subjects_list"] = subjects  # Store subjects list in session

        return render_template(
            "result.html",
            user=user,
            results=results,
            subjects=subjects,
            summary=summary,
            max_marks=max_marks,
        )

    except Exception as e:
        print(f"Error : {str(e)}"),
        return render_template(
            "dashboard.html",
            user=user,
        )


# Student Detail View Route
@app.route("/student/<enrollment_no>")
def student_detail(enrollment_no):
    if "user_id" not in session:
        return redirect(url_for("login"))

    user = User.query.get(session["user_id"])
    if not user:
        session.clear()
        return redirect(url_for("login"))

    # Get data from session (already processed)
    results = session.get("processed_results")
    subjects = session.get("subjects_list")
    max_marks = session.get("max_marks")

    # Find student from already processed results
    student = None
    for s in results:
        if s["enrollment_no"] == int(enrollment_no):
            student = s
            break

    # Get class statistics for chart
    try:
        filepath = session.get("uploaded_file")
        if filepath and os.path.exists(filepath):
            if filepath.endswith(".xlsx"):
                df = pd.read_excel(filepath, engine="openpyxl")
            else:
                df = pd.read_excel(filepath)

            class_stats = {"subject_max": {}, "subject_min": {}, "subject_avg": {}}

            for subj in subjects:
                if subj in df.columns:
                    class_stats["subject_max"][subj] = float(df[subj].max())
                    class_stats["subject_min"][subj] = float(df[subj].min())
                    class_stats["subject_avg"][subj] = float(round(df[subj].mean(), 2))
        else:
            class_stats = {}
    except Exception as e:
        print(f"Error : {str(e)}")
        return render_template(
            url_for("process_results"),
            user=user,
        )

    return render_template(
        "student_detail.html",
        user=user,
        student=student,
        subjects=subjects,
        total_marks=student["total"],
        percentage=student["percentage"],
        grade=student["grade"],
        status=student["status"],
        rank=student["rank"],
        max_marks=max_marks,
        class_stats=class_stats,
    )


# Email Result Route
@app.route("/send-student-result", methods=["POST"])
def send_student_result():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user = User.query.get(session["user_id"])
    if not user:
        session.clear()
        return redirect(url_for("login"))

    try:
        enrollment_no = int(request.form.get("enrollment_no"))
        student_email = request.form.get("student_email").strip()

        # Get student from processed results
        results = session.get("processed_results")
        subjects = session.get("subjects_list")
        max_marks = session.get("max_marks")

        # Find student
        student = None
        for s in results:
            if s["enrollment_no"] == int(enrollment_no):
                student = s
                break

        # Generate PDF
        pdf_content = generate_student_pdf(student, subjects, max_marks)

        # Compose email body
        email_body = f"""
ACADEMIC RESULTS - {student['name'].upper()}
{'-' * 30}
Your detailed mark sheet is attached as a PDF file.
        
{'-' * 30}
This is an automated message. Please do not reply.
Generated by ResultVista.
        """

        # Send plain text email
        msg = Message(
            subject=f"Academic Results - {student['name'].upper()}",
            recipients=[student_email],
            body=email_body,
            sender=app.config.get("MAIL_DEFAULT_SENDER"),
        )

        # Attach PDF
        msg.attach(
            filename=f"Result_{enrollment_no}.pdf",
            content_type="application/pdf",
            data=pdf_content,
        )

        mail.send(msg)
        flash("Result PDF has been sent successfully.")

    except Exception as e:
        flash("Failed to send result PDF.")
        print(f"Error sending email: {str(e)}")
        if enrollment_no:
            return redirect(url_for("student_detail", enrollment_no=enrollment_no))
        else:
            return redirect(url_for("process_results"))

    if enrollment_no:
        return redirect(url_for("student_detail", enrollment_no=enrollment_no))
    else:
        return redirect(url_for("process_results"))


# function to generate PDF
def generate_student_pdf(student, subjects, max_marks):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    from io import BytesIO
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    # Add content to PDF
    p.setFont("Helvetica-Bold", 16)
    p.drawString(100, height - 100, f"Academic Result - {student['name'].upper()}")
    p.setFont("Helvetica", 12)

    # Student details
    y_position = height - 150
    p.drawString(100, y_position, f"Name: {student['name'].upper()}")
    y_position -= 20
    p.drawString(100, y_position, f"Enrollment: {student['enrollment_no']}")
    y_position -= 20
    p.drawString(100, y_position, f"Rank: {student['rank']}")
    y_position -= 40

    # Table header
    p.drawString(100, y_position, "Subject")
    p.drawString(200, y_position, f"Marks (Max: {max_marks})")
    y_position -= 20

    # Subjects
    for subject in subjects:
        marks = student["subjects"][subject]
        p.drawString(100, y_position, subject.split()[0].capitalize())
        p.drawString(250, y_position, str(marks))
        y_position -= 20

    # Summary
    y_position -= 20
    p.drawString(100, y_position, f"Total Marks: {student['total']}")
    y_position -= 20
    p.drawString(100, y_position, f"Percentage: {student['percentage']}%")
    y_position -= 20
    p.drawString(100, y_position, f"Grade: {student['grade']}")
    y_position -= 20
    p.drawString(100, y_position, f"Status: {student['status']}")

    # Chart generation
    result = session.get("processed_results")
    subjects_names = [subj.split()[0].capitalize() for subj in subjects]
    marks_obtained = [student["subjects"][subj] for subj in subjects]
    max_marks_each_subj = [
        max(student["subjects"][subj] for student in result) for subj in subjects
    ]
    min_marks_each_subj = [
        min(student["subjects"][subj] for student in result) for subj in subjects
    ]
    average_marks_each_subj = [
        round(sum(student["subjects"][subj] for student in result) / len(result), 2)
        for subj in subjects
    ]

    plt.plot(
        subjects_names, marks_obtained, marker="o", label="Student Marks", color="blue"
    )
    plt.plot(
        subjects_names,
        max_marks_each_subj,
        marker="o",
        label="Max Marks",
        color="green",
    )
    plt.plot(
        subjects_names, min_marks_each_subj, marker="o", label="Min Marks", color="red"
    )
    plt.plot(
        subjects_names,
        average_marks_each_subj,
        marker="o",
        label="Average Marks",
        color="orange",
    )

    plt.xlabel("Subjects")
    plt.ylabel("Marks")
    plt.title(f"Performance Chart for {student['name'].upper()}")
    plt.legend()
    plt.grid(True)

    # Save chart to buffer
    chart_buffer = BytesIO()
    plt.savefig(chart_buffer, format="png")
    chart_buffer.seek(0)
    plt.close()

    # Add chart to PDF
    p.drawImage(ImageReader(chart_buffer), 100, 200, width=300, height=200)

    p.save()
    buffer.seek(0)
    return buffer.read()


# Chart visualization route
@app.route("/charts", methods=["GET", "POST"])
def show_charts():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user = User.query.get(session["user_id"])
    if not user:
        session.clear()
        return redirect(url_for("login"))

    # Check if we have uploaded file data
    if "uploaded_file" not in session:
        return redirect(url_for("dashboard"))

    filepath = session.get("uploaded_file")

    # Check if file actually exists
    if not filepath or not os.path.exists(filepath):
        # Clear invalid session data
        session.pop("uploaded_file", None)
        session.pop("max_marks", None)
        session.pop("file_stats", None)
        session.pop("preview_data", None)
        return redirect(url_for("dashboard"))

    max_marks = session.get("max_marks")

    try:
        # Read the uploaded file
        if filepath.endswith(".xlsx"):
            df = pd.read_excel(filepath, engine="openpyxl")
        else:
            df = pd.read_excel(filepath)

        # Process data for charts
        subjects = []
        enrollment_col = None
        name_col = None

        # Identify subject columns
        for col in df.columns:
            if "marks" in str(col).lower():
                subjects.append(str(col))

        # Find enrollment column
        for col in df.columns:
            if "enrollment" in str(col).lower():
                enrollment_col = col
                break

        # Find name column
        for col in df.columns:
            if "name" in str(col).lower():
                name_col = col
                break

        # Prepare chart data
        chart_data = {
            "subjects": subjects,
            "subject_avg": [],
            "subject_max": [],
            "subject_min": [],
            "grade_distribution": {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0},
            "pass_fail": {"PASS": 0, "FAIL": 0},
            "top_students": [],
            "performance_ranges": {
                "90-100": 0,
                "75-89": 0,
                "60-74": 0,
                "33-59": 0,
                "0-32": 0,
            },
        }

        # Calculate subject statistics
        for subject in subjects:
            # get marks column and drop NaN values
            marks = df[subject].dropna()
            # Convert numpy types to Python native types
            avg_val = float(round(marks.mean(), 2))
            max_val = float(round(marks.max()))
            min_val = float(round(marks.min()))

            chart_data["subject_avg"].append(avg_val)
            chart_data["subject_max"].append(max_val)
            chart_data["subject_min"].append(min_val)

        # Calculate individual student data for charts
        student_percentages = []

        for _, row in df.iterrows():
            # Calculate percentage
            total_marks = 0
            for subject in subjects:
                marks = float(row[subject])
                total_marks += marks

            percentage = float(
                round((total_marks / (len(subjects) * max_marks)) * 100, 2)
            )
            student_percentages.append(percentage)

            # Grade distribution
            if percentage >= 90:
                grade = "A"
            elif percentage >= 75:
                grade = "B"
            elif percentage >= 60:
                grade = "C"
            elif percentage >= 33:
                grade = "D"
            else:
                grade = "F"

            chart_data["grade_distribution"][grade] += 1

            # Pass/Fail
            status = "PASS" if percentage >= 33 else "FAIL"
            chart_data["pass_fail"][status] += 1

            # Performance ranges
            if percentage >= 90:
                chart_data["performance_ranges"]["90-100"] += 1
            elif percentage >= 75:
                chart_data["performance_ranges"]["75-89"] += 1
            elif percentage >= 60:
                chart_data["performance_ranges"]["60-74"] += 1
            elif percentage >= 33:
                chart_data["performance_ranges"]["33-59"] += 1
            else:
                chart_data["performance_ranges"]["0-32"] += 1

            # Collect top 5 students
            student_name = str(row[name_col])
            student_enrollment = str(row[enrollment_col])

            chart_data["top_students"].append(
                {
                    "name": student_name,
                    "enrollment": student_enrollment,
                    "percentage": float(percentage),
                    "grade": grade,
                }
            )

        # Sort and get top 5 students
        chart_data["top_students"].sort(key=lambda x: x["percentage"], reverse=True)
        chart_data["top_students"] = chart_data["top_students"][:5]

        # Calculate overall statistics
        chart_data["overall_avg"] = float(
            round(sum(student_percentages) / len(student_percentages), 2)
        )
        chart_data["overall_max"] = float(max(student_percentages))
        chart_data["overall_min"] = float(min(student_percentages))

        # Calculate pass percentage
        chart_data["pass_percentage"] = float(
            round((chart_data["pass_fail"]["PASS"] / len(df)) * 100, 2)
        )

        # Convert all numpy types to Python native types
        chart_data["grade_distribution"] = {
            k: int(v) for k, v in chart_data["grade_distribution"].items()
        }
        chart_data["pass_fail"] = {
            k: int(v) for k, v in chart_data["pass_fail"].items()
        }
        chart_data["performance_ranges"] = {
            k: int(v) for k, v in chart_data["performance_ranges"].items()
        }

        # Render the template
        return render_template(
            "charts.html",
            user=user,
            chart_data=chart_data,
            max_marks=max_marks,
        )

    except Exception as e:
        return render_template(
            "dashboard.html",
            user=user,
            # upload_error=f"Error generating charts: {str(e)}",
        )


# Logout route
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# Delete account route
@app.route("/delete-account", methods=["POST"])
def delete_account():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user = User.query.get(session["user_id"])
    if user:
        # Delete user from database
        send_delete_account_email(user.email, user.name)
        db.session.delete(user)
        db.session.commit()

        # Clear session
        session.clear()

    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
