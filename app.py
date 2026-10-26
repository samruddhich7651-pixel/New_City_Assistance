from google import genai
import traceback
import os
from functools import wraps
import secrets
from authlib.integrations.flask_client import OAuth
from psycopg2.extras import DictCursor


from flask import (
    Flask, render_template, request, flash,
    redirect, url_for, session, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from psycopg2.extras import DictCursor

from database import get_db_connection

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv(
    "SECRET_KEY",
    "cityassist_development_secret_key"
)
oauth = OAuth(app)

google = oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid email profile"
    }
)

import os

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# ================= COMMON HELPERS =================

def login_required(role=None):
    if role == "provider" and "provider_id" not in session:
        flash("Please log in with your provider account.")
        return False

    if role == "user" and "user_id" not in session:
        flash("Please log in to continue.")
        return False

    return True


def get_dict_cursor(connection):
    return connection.cursor(cursor_factory=DictCursor)


# ================= HOME =================

@app.route("/")
def home():
    return render_template("home.html")


# ================= ABOUT =================

@app.route("/about")
def about():
    return render_template("about.html")


# ================= LOGIN =================

@app.route("/choose-role")
def choose_role():
    return render_template("choose_role.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    selected_role = request.args.get("role", "").strip().lower()

    if selected_role not in ["user", "provider"]:
        selected_role = request.form.get("role", "").strip().lower()

    if selected_role not in ["user", "provider"]:
        return redirect(url_for("choose_role"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please enter email and password.")
            return redirect(url_for("login", role=selected_role))

        connection = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor()

            if selected_role == "user":
                cursor.execute("""
                    SELECT id, first_name, mobile, email, password_hash, city, COALESCE(is_active, TRUE)
                    FROM users
                    WHERE email = %s
                """, (email,))

                user = cursor.fetchone()
                cursor.close()

                if user and user[6] and check_password_hash(user[4], password):
                    session.clear()
                    session["user_id"] = user[0]
                    session["user_name"] = user[1]
                    session["mobile"] = user[2]
                    session["email"] = user[3]
                    session["user_city"] = user[5]
                    session["role"] = "user"

                    return redirect(url_for("dashboard"))

            else:
                cursor.execute("""
                    SELECT id, owner_name, password_hash, is_verified, COALESCE(is_active, TRUE)
                    FROM providers
                    WHERE email = %s
                """, (email,))

                provider = cursor.fetchone()
                cursor.close()

                if provider and provider[4] and check_password_hash(provider[2], password):
                    session.clear()
                    session["provider_id"] = provider[0]
                    session["provider_name"] = provider[1]
                    session["role"] = "provider"

                    if not provider[3]:
                        flash(
                            "Your provider account is pending verification. "
                            "You can submit services, but they will not be "
                            "visible until approval."
                        )

                    return redirect(url_for("provider_dashboard"))

            flash("Invalid email or password.")

        except Exception:
            flash("Login failed. Please try again.")

        finally:
            if connection:
                connection.close()

        return redirect(url_for("login", role=selected_role))

    return render_template("login.html", selected_role=selected_role)

@app.route("/login/google")
def google_login():
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/login/google/callback")
def google_callback():
    connection = None
    cursor = None

    try:
        token = google.authorize_access_token()
        user_info = google.userinfo()

        email = user_info.get("email")
        email_verified = user_info.get("email_verified", False)

        if not email or not email_verified:
            flash("Google email could not be verified. Please try again.", "error")
            return redirect(url_for("login", role="user"))

        first_name = user_info.get("given_name") or email.split("@")[0]
        last_name = user_info.get("family_name") or None

        connection = get_db_connection()
        cursor = connection.cursor(cursor_factory=DictCursor)

        cursor.execute(
            """
            SELECT id, first_name, mobile, email, city,
                   COALESCE(is_active, TRUE) AS is_active
            FROM users
            WHERE LOWER(email) = LOWER(%s)
            """,
            (email,)
        )
        user = cursor.fetchone()

        if user:
            if not user["is_active"]:
                flash("Your account is inactive. Please contact support.", "error")
                return redirect(url_for("login", role="user"))

            user_id = user["id"]
            first_name = user["first_name"]
            mobile = user["mobile"]
            city = user["city"]

        else:
            random_password = secrets.token_urlsafe(32)
            password_hash = generate_password_hash(random_password)

            cursor.execute(
                """
                INSERT INTO users
                    (first_name, last_name, email, mobile, city, password_hash)
                VALUES (%s, %s, %s, NULL, NULL, %s)
                RETURNING id
                """,
                (first_name, last_name, email, password_hash)
            )

            user_id = cursor.fetchone()["id"]
            mobile = None
            city = None
            connection.commit()

        session.clear()
        session["user_id"] = user_id
        session["user_name"] = first_name
        session["mobile"] = mobile
        session["email"] = email
        session["user_city"] = city
        session["role"] = "user"

        return redirect(url_for("dashboard"))

    except Exception:
        if connection:
            connection.rollback()

        app.logger.exception("Google login failed")
        flash("Google login failed. Please try again.", "error")
        return redirect(url_for("login", role="user"))

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ================= LOGOUT =================

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("choose_role"))


# ================= USER REGISTRATION =================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        mobile = request.form.get("mobile", "").strip()
        city = request.form.get("city", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not all([first_name, email, password, confirm_password]):
            flash("Please fill all required fields.")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must contain at least 8 characters.")
            return redirect(url_for("register"))

        connection = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor()

            cursor.execute(
                """
                INSERT INTO users
                (first_name, last_name, email, mobile, city, password_hash)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    first_name,
                    last_name,
                    email,
                    mobile,
                    city,
                    generate_password_hash(password)
                )
            )

            connection.commit()
            cursor.close()

            flash("Registration successful! Please log in.")
            return redirect(url_for("login", role="user"))

        except Exception:
            if connection:
                connection.rollback()

            flash("Registration failed. This email may already be registered.")
            return redirect(url_for("register"))

        finally:
            if connection:
                connection.close()

    return render_template("register.html")

# ================= PROVIDER REGISTRATION =================

@app.route("/provider-register", methods=["GET", "POST"])
def provider_register():
    if request.method == "POST":
        business_name = request.form.get("business_name", "").strip()
        owner_name = request.form.get("owner_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        mobile = request.form.get("mobile", "").strip()
        category = request.form.get("category", "").strip()
        address = request.form.get("address", "").strip()
        city = request.form.get("city", "").strip()
        description = request.form.get("description", "").strip()
        password = request.form.get("password", "")

        if not all([
            business_name, owner_name, email, mobile, password
        ]):
            flash("Please fill all required provider details.")
            return redirect(url_for("provider_register"))

        if len(password) < 8:
            flash("Password must contain at least 8 characters.")
            return redirect(url_for("provider_register"))

        connection = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor()

            cursor.execute(
                """
                INSERT INTO providers
                (
                    business_name, owner_name, email, mobile,
                    category, address, city, description, password_hash
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    business_name,
                    owner_name,
                    email,
                    mobile,
                    category,
                    address,
                    city,
                    description,
                    generate_password_hash(password)
                )
            )

            connection.commit()
            cursor.close()

            flash(
                "Provider registration successful! "
                "Please log in. Your account is pending verification."
            )
            return redirect(url_for("login"))

        except Exception:
            if connection:
                connection.rollback()

            flash(
                "Provider registration failed. "
                "This email may already be registered."
            )
            return redirect(url_for("provider_register"))

        finally:
            if connection:
                connection.close()

    return render_template("provider_register.html")


# ================= USER DASHBOARD =================


# ================= USER DASHBOARD =================

@app.route("/dashboard")
def dashboard():
    if not login_required("user"):
        return redirect(url_for("login", role="user"))

    connection = None
    recommended_services = []
    nearby_services = []

    user_city = session.get("user_city", "")

    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)

        # Latest verified services
        cursor.execute("""
            SELECT
                s.id,
                s.service_name,
                s.category,
                s.price,
                s.city,
                s.address
            FROM services s
            JOIN providers p ON s.provider_id = p.id
            WHERE s.is_active = TRUE
              AND s.is_verified = TRUE
              AND p.is_verified = TRUE
            ORDER BY s.created_at DESC
            LIMIT 4
        """)
        recommended_services = cursor.fetchall()

        # Services available in user's city
        if user_city:
            cursor.execute("""
                SELECT
                    s.id,
                    s.service_name,
                    s.category,
                    s.price,
                    s.city,
                    s.address
                FROM services s
                JOIN providers p ON s.provider_id = p.id
                WHERE s.is_active = TRUE
                  AND s.is_verified = TRUE
                  AND p.is_verified = TRUE
                  AND s.city ILIKE %s
                ORDER BY s.created_at DESC
                LIMIT 4
            """, (f"%{user_city}%",))

            nearby_services = cursor.fetchall()

        cursor.close()

    except Exception:
      flash("Unable to load services. Please try again.")


    finally:
        if connection:
            connection.close()

    return render_template(
        "dashboard.html",
        user_city=user_city or "Your City",
        recommended_services=recommended_services,
        nearby_services=nearby_services
    )



# ================= EXPLORE SERVICES =================


@app.route("/explore")
def explore():
    if not login_required("user"):
        return redirect(url_for("login"))

    search_query = request.args.get("search", "").strip()
    city = request.args.get("city", "").strip()
    category = request.args.get("category", "").strip()

    if not city:
        city = session.get("user_city", "")

    connection = None

    
    connection = None

    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)

        sql = """
            SELECT
                s.id,
                s.service_name,
                s.category,
                s.price,
                s.address,
                s.city,
                s.description,
                s.contact,
                p.business_name,
                s.is_verified
            FROM services s
            JOIN providers p ON s.provider_id = p.id
            WHERE s.is_active = TRUE
              AND s.is_verified = TRUE
              AND p.is_verified = TRUE
        """

        params = []

        if search_query:
            sql += """
                AND (
                    s.service_name ILIKE %s
                    OR s.description ILIKE %s
                )
            """
            params.extend([
                f"%{search_query}%",
                f"%{search_query}%"
            ])

        if city:
            sql += " AND s.city ILIKE %s"
            params.append(f"%{city}%")

        if category:
            sql += " AND s.category ILIKE %s"
            params.append(f"%{category}%")

        sql += " ORDER BY s.created_at DESC"

        cursor.execute(sql, tuple(params))
        services = cursor.fetchall()

        cursor.execute("""
            SELECT category_name
            FROM service_categories
            WHERE is_active = TRUE
            ORDER BY category_name
        """)

        categories = cursor.fetchall()
        cursor.close()

        return render_template(
            "explore.html",
            services=services,
            categories=categories,
            search_query=search_query,
            city=city,
            user_city=session.get("user_city", "Your City"),
            selected_category=category
        )

    except Exception as e:
        print("EXPLORE ERROR:", e)
        flash("Unable to load services. Please try again.")

        return render_template(
            "explore.html",
            services=[],
            categories=[],
            search_query=search_query,
            city=city,
            user_city=session.get("user_city", "Your City"),
            selected_category=category
        )

    finally:
        if connection:
            connection.close()



# ================= SERVICE DETAILS =================
@app.route("/service-details/<int:service_id>")
def service_details(service_id):
    if not login_required("user"):
        return redirect(url_for("login"))

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)

        cursor.execute(
            """
            SELECT
                s.id,
                s.service_name,
                s.category,
                s.price,
                s.address,
                s.city,
                s.description,
                s.contact,
                p.business_name
            FROM services s
            JOIN providers p ON s.provider_id = p.id
            WHERE s.id = %s
              AND s.is_active = TRUE
              AND s.is_verified = TRUE
              AND p.is_verified = TRUE
            """,
            (service_id,)
        )

        service = cursor.fetchone()

        if not service:
            abort(404)

        cursor.execute(
            """
            SELECT id
            FROM favourites
            WHERE user_id = %s AND service_id = %s
            """,
            (session["user_id"], service_id)
        )

        is_favourite = cursor.fetchone() is not None

        return render_template(
            "service_details.html",
            service=service,
            is_favourite=is_favourite
        )

    except Exception as e:
        print("SERVICE DETAILS ERROR:", repr(e))
        abort(500)

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# ================= AI ASSISTANT =================

@app.route("/ai-assistant")
def ai_assistant():
    if not login_required("user"):
        return redirect(url_for("login"))

    return render_template("ai_assistant.html")


@app.route("/api/ai-chat", methods=["POST"])
def ai_chat():
    if not login_required("user"):
        return {"error": "Please login first."}, 401

    data = request.get_json() or {}
    message = data.get("message", "").strip()

    if not message:
        return {"error": "Please enter a message."}, 400

    connection = None

    try:
        # Fetch verified services from database
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)
        print("CURSOR TYPE:", type(cursor))

        sql = """
            SELECT
                s.service_name,
                s.category,
                s.price,
                s.address,
                s.city,
                s.description,
                p.business_name
            FROM services s
            JOIN providers p ON s.provider_id = p.id
            WHERE s.is_active = TRUE
              AND s.is_verified = TRUE
              AND p.is_verified = TRUE
            ORDER BY s.created_at DESC
            LIMIT 100
        """

        cursor.execute(sql)
        services = cursor.fetchall()
        cursor.close()

        # Prepare safe service information (no contact numbers)
        service_data = []

        for service in services:
            service_data.append({
                "service_name": service["service_name"],
                "category": service["category"],
                "price": service["price"],
                "address": service["address"],
                "city": service["city"],
                "description": service["description"],
                "business_name": service["business_name"]
            })

        prompt = f"""
You are CityAssist AI, the official AI assistant of CityAssist.

ABOUT CITYASSIST:
CityAssist helps people who have shifted to a new city find
essential local services.

WEBSITE FEATURES:
- Home: Introduction to CityAssist.
- About: Information about the platform.
- Login/Register: User account registration and login.
- Dashboard: Useful service information.
- Explore: Search services by keyword, city and category.
- Service Details: View service information.
- Nearby: Explore local services.
- Favourites: Save favourite services.
- AI Assistant: Get guidance about CityAssist and local services.
- Emergency: Emergency-related information.
- Profile and Settings: Manage user account.

PROVIDER FEATURES:
- Providers can register their businesses.
- Providers can add services with name, category, price,
  address, city and description.
- New services require admin verification.
- Providers can manage their services from the provider dashboard.

ADMIN FEATURES:
- Admin can manage users and providers.
- Admin can verify services.
- Admin can manage categories and reports.
- Admin can update website settings.

DATABASE SERVICES:
The following information comes from CityAssist's actual database.
Only these listings are available for recommendations:

{service_data}

IMPORTANT RULES:
1. Reply in simple Hinglish using English letters.
2. If the user writes entirely in English, reply in English.
3. Keep answers short and point-wise.
4. Answer CityAssist website questions using the website knowledge.
5. For local service requests, use the database listings above.
6. Match the user's requested city, service and category carefully.
7. Show only matching services from the database.
8. You may show business name, service name, category, price,
   address, city and description.
9. NEVER show or reveal contact numbers.
10. Never invent service listings, prices, addresses or details.
11. If no matching listing exists, clearly say that no matching
    verified listing was found on CityAssist.
12. Do not claim that all services in a city are listed.
13. If the user asks how to use CityAssist, explain the relevant
    website feature step-by-step.
14. For unrelated general questions, answer briefly and helpfully.

USER QUESTION:
{message}
"""

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

        return {"reply": response.text}

    except Exception as e:
        print("Gemini Error:", e)
        return {
            "error": "AI Assistant is temporarily unavailable."
        }, 500

    finally:
        if connection:
            connection.close()

# ================= NEARBY SERVICES =================

@app.route("/nearby")
def nearby():
    if not login_required("user"):
        return redirect(url_for("login"))

    city = request.args.get("city", "").strip()
    category = request.args.get("category", "").strip()

    if not city:
        city = session.get("user_city", "")

    services = []
    categories = []
    connection = None

    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)

        sql = """
            SELECT
                s.id,
                s.service_name,
                s.category,
                s.price,
                s.address,
                s.city,
                s.contact,
                p.business_name,
                s.latitude,
                s.longitude,
                s.description
            FROM services s
            JOIN providers p ON s.provider_id = p.id
            WHERE s.is_active = TRUE
              AND s.is_verified = TRUE
              AND p.is_verified = TRUE
              AND s.latitude IS NOT NULL
              AND s.longitude IS NOT NULL
        """

        params = []

        if city:
            sql += " AND s.city ILIKE %s"
            params.append(f"%{city}%")

        if category:
            sql += " AND s.category ILIKE %s"
            params.append(f"%{category}%")

        sql += " ORDER BY s.created_at DESC"

        cursor.execute(sql, tuple(params))
        services = cursor.fetchall()

        cursor.execute("""
            SELECT category_name
            FROM service_categories
            WHERE is_active = TRUE
            ORDER BY category_name
        """)

        categories = cursor.fetchall()
        cursor.close()

    except Exception as e:
        print("NEARBY ERROR:", e)
        flash("Unable to load nearby services.")

    finally:
        if connection:
            connection.close()

    return render_template(
        "nearby.html",
        services=services,
        categories=categories,
        city=city,
        selected_category=category
    )

# ================= EMERGENCY =================

@app.route("/emergency")
def emergency():
    if not login_required("user"):
        return redirect(url_for("login"))

    return render_template("emergency.html")


# ================= FAVOURITES =================

@app.route("/favourites")
def favourites():
    if not login_required("user"):
        return redirect(url_for("login"))

    connection = None

    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)

        cursor.execute(
            """
            SELECT
                s.id,
                s.service_name,
                s.category,
                s.price,
                s.address,
                s.city,
                s.description,
                s.contact,
                p.business_name
            FROM favourites f
            JOIN services s ON f.service_id = s.id
            JOIN providers p ON s.provider_id = p.id
            WHERE f.user_id = %s
              AND s.is_active = TRUE
              AND s.is_verified = TRUE
              AND p.is_verified = TRUE
            ORDER BY f.created_at DESC
            """,
            (session["user_id"],)
        )

        saved_services = cursor.fetchall()
        cursor.close()

        return render_template(
            "favourites.html",
            services=saved_services
        )

    except Exception:
        flash("Unable to load your favourites. Please try again.")
        return render_template(
            "favourites.html",
            services=[]
        )

    finally:
        if connection:
            connection.close()


# ================= ADD OR REMOVE FAVOURITE =================
@app.route("/favourites/toggle/<int:service_id>", methods=["POST"])
def toggle_favourite(service_id):
    if not login_required("user"):
        return redirect(url_for("login"))

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("""
            SELECT id FROM favourites
            WHERE user_id = %s AND service_id = %s
        """, (session["user_id"], service_id))

        existing = cursor.fetchone()

        if existing:
            cursor.execute("""
                DELETE FROM favourites
                WHERE user_id = %s AND service_id = %s
            """, (session["user_id"], service_id))

            message = "Service removed from favourites."

        else:
            cursor.execute("""
                SELECT s.id
                FROM services s
                JOIN providers p ON s.provider_id = p.id
                WHERE s.id = %s
                  AND s.is_active = TRUE
                  AND s.is_verified = TRUE
                  AND p.is_verified = TRUE
            """, (service_id,))

            if not cursor.fetchone():
                flash("This service is not available to save.")
                return redirect(url_for("explore"))

            cursor.execute("""
                INSERT INTO favourites (user_id, service_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id, service_id) DO NOTHING
            """, (session["user_id"], service_id))

            message = "Service added to favourites."

        connection.commit()
        print("FAVOURITE SUCCESS:", message)
        flash(message)

    except Exception as e:
        print("FAVOURITE ERROR:", repr(e))

        if connection:
            connection.rollback()

        flash("Unable to update favourites. Please try again.")

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

    return redirect(request.referrer or url_for("favourites"))

# ================= MY ADDRESSES =================

@app.route("/addresses", methods=["GET", "POST"])
def my_addresses():
    if not login_required("user"):
        return redirect(url_for("login"))

    connection = None

    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)

        if request.method == "POST":
            address_name = request.form.get("address_name", "").strip()
            full_address = request.form.get("full_address", "").strip()
            city = request.form.get("city", "").strip()
            pincode = request.form.get("pincode", "").strip()

            if not all([address_name, full_address, city]):
                flash("Please fill all required address details.")
                return redirect(url_for("my_addresses"))

            cursor.execute(
                """
                INSERT INTO addresses
                (user_id, address_name, full_address, city, pincode)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    session["user_id"],
                    address_name,
                    full_address,
                    city,
                    pincode
                )
            )

            connection.commit()
            flash("Address saved successfully!")
            return redirect(url_for("my_addresses"))

        cursor.execute(
            """
            SELECT id, address_name, full_address, city, pincode
            FROM addresses
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (session["user_id"],)
        )

        addresses = cursor.fetchall()
        cursor.close()

        return render_template(
            "addresses.html",
            addresses=addresses
        )

    except Exception:
        traceback.print_exc()
        if connection:
            connection.rollback()

        flash("Unable to process address. Please try again.")
        return redirect(url_for("my_addresses"))

    finally:
     if connection:
        connection.close()

@app.route("/search-history")
def search_history():
    if not login_required("user"):
        return redirect(url_for("login"))

    connection = None

    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)

        cursor.execute("""
            SELECT id, search_query, city, created_at
            FROM search_history
            WHERE user_id = %s
            ORDER BY created_at DESC
        """, (session["user_id"],))

        searches = cursor.fetchall()
        cursor.close()

        return render_template("search_history.html", searches=searches)

    except Exception:
        if connection:
            connection.rollback()
        flash("Unable to load search history. Please try again.")
        return redirect(url_for("profile"))

    finally:
        if connection:
            connection.close()

@app.route("/settings")
def settings():
    if not login_required("user"):
        return redirect(url_for("login"))

    return render_template("settings.html")

@app.route("/privacy-security")
def privacy_security():
    if not login_required("user"):
        return redirect(url_for("login"))

    return render_template("privacy_security.html")

@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    if not login_required("user"):
        return redirect(url_for("login"))

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not current_password or not new_password or not confirm_password:
            flash("Please fill all password fields.")
            return redirect(url_for("change_password"))

        if len(new_password) < 8:
            flash("New password must be at least 8 characters long.")
            return redirect(url_for("change_password"))

        if new_password != confirm_password:
            flash("New passwords do not match.")
            return redirect(url_for("change_password"))

        connection = None

        try:
            connection = get_db_connection()
            cursor = get_dict_cursor(connection)

            cursor.execute(
                "SELECT password_hash FROM users WHERE id = %s",
                (session["user_id"],)
            )
            user = cursor.fetchone()

            if not user or not check_password_hash(
                user["password_hash"], current_password
            ):
                flash("Current password is incorrect.")
                return redirect(url_for("change_password"))

            new_hash = generate_password_hash(new_password)

            cursor.execute("""
                UPDATE users
                SET password_hash = %s
                WHERE id = %s
            """, (new_hash, session["user_id"]))

            connection.commit()
            cursor.close()

            flash("Password changed successfully!")
            return redirect(url_for("settings"))

        except Exception:
            if connection:
                connection.rollback()
            flash("Unable to change password. Please try again.")
            return redirect(url_for("change_password"))

        finally:
            if connection:
                connection.close()

    return render_template("change_password.html")

@app.route("/notification-preferences", methods=["GET", "POST"])
def notification_preferences():
    if not login_required("user"):
        return redirect(url_for("login"))

    connection = None

    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)

        if request.method == "POST":
            email_notifications = request.form.get("email_notifications") == "on"
            service_notifications = request.form.get("service_notifications") == "on"
            emergency_notifications = request.form.get("emergency_notifications") == "on"

            cursor.execute("""
                UPDATE users
                SET email_notifications = %s,
                    service_notifications = %s,
                    emergency_notifications = %s
                WHERE id = %s
            """, (
                email_notifications,
                service_notifications,
                emergency_notifications,
                session["user_id"]
            ))

            connection.commit()
            flash("Notification preferences saved successfully!")
            return redirect(url_for("notification_preferences"))

        cursor.execute("""
            SELECT email_notifications, service_notifications,
                   emergency_notifications
            FROM users
            WHERE id = %s
        """, (session["user_id"],))

        preferences = cursor.fetchone()
        cursor.close()

        return render_template(
            "notification_preferences.html",
            preferences=preferences
        )

    except Exception:
        if connection:
            connection.rollback()
        flash("Unable to update notification preferences.")
        return redirect(url_for("settings"))

    finally:
        if connection:
            connection.close()

            
# ================= PROVIDER DASHBOARD =================

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if not login_required("user"):
        return redirect(url_for("login"))

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)

        if request.method == "POST":
            first_name = request.form.get("first_name", "").strip()
            last_name = request.form.get("last_name", "").strip()
            mobile = request.form.get("mobile", "").strip()
            city = request.form.get("city", "").strip()

            if not first_name:
                flash("First name is required.")
                return redirect(url_for("profile"))

            cursor.execute("""
                UPDATE users
                SET first_name = %s,
                    last_name = %s,
                    mobile = %s,
                    city = %s
                WHERE id = %s
            """, (
                first_name,
                last_name,
                mobile,
                city,
                session["user_id"]
            ))

            connection.commit()

            flash("Profile updated successfully!")
            return redirect(url_for("profile"))

        cursor.execute("""
            SELECT first_name, last_name, email, mobile, city
            FROM users
            WHERE id = %s
        """, (session["user_id"],))

        user = cursor.fetchone()

        return render_template("profile.html", user=user)

    except Exception as e:
        print("PROFILE ERROR:", repr(e))

        if connection:
            connection.rollback()

        flash("Unable to update profile. Please try again.")
        return redirect(url_for("dashboard"))

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route("/my-reviews")
def my_reviews():
    if not login_required("user"):
        return redirect(url_for("login"))

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)

        cursor.execute("""
            SELECT
                r.id,
                r.rating,
                r.comment,
                r.created_at,
                s.service_name,
                s.category,
                s.city
            FROM reviews r
            JOIN services s ON r.service_id = s.id
            WHERE r.user_id = %s
            ORDER BY r.created_at DESC
        """, (session["user_id"],))

        reviews = cursor.fetchall()

        return render_template("my_reviews.html", reviews=reviews)

    except Exception as e:
        print("MY REVIEWS ERROR:", repr(e))
        if connection:
            connection.rollback()
        flash("Unable to load your reviews. Please try again.")
        return redirect(url_for("profile"))

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()            

@app.route("/provider-dashboard")
def provider_dashboard():
    if not login_required("provider"):
        return redirect(url_for("login"))

    connection = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE is_active = TRUE),
                COUNT(*) FILTER (WHERE is_verified = TRUE)
            FROM services
            WHERE provider_id = %s
            """,
        
            (session["provider_id"],)
        )

        stats = cursor.fetchone()

        cursor.execute(
            """
            SELECT is_verified
            FROM providers
            WHERE id = %s
            """,
            (session["provider_id"],)
        )

        provider = cursor.fetchone()
        cursor.close()

        return render_template(
            "provider_dashboard.html",
            stats=stats,
            provider_verified=provider[0] if provider else False
        )

    except Exception:
      traceback.print_exc()

    if connection:
        connection.rollback()

    flash("Unable to process address. Please try again.")
    return redirect(url_for("my_addresses"))

# ================= MANAGE PROVIDER SERVICES =================
@app.route("/manage-services", methods=["GET", "POST"])
def manage_services():
    if not login_required("provider"):
        return redirect(url_for("login"))

    connection = None

    if request.method == "POST":
        service_name = request.form.get("service_name", "").strip()
        category = request.form.get("category", "").strip()
        price = request.form.get("price", "").strip()
        address = request.form.get("address", "").strip()
        city = request.form.get("city", "").strip()
        contact = request.form.get("contact", "").strip()
        description = request.form.get("description", "").strip()
        latitude = request.form.get("latitude", "").strip()
        longitude = request.form.get("longitude", "").strip()

        if not all([service_name, category, address, city, description]):
            flash("Please fill all required service details.")
            return redirect(url_for("manage_services"))

        try:
            service_price = float(price) if price else None

            if service_price is not None and service_price < 0:
                flash("Price cannot be negative.")
                return redirect(url_for("manage_services"))

            service_latitude = float(latitude) if latitude else None
            service_longitude = float(longitude) if longitude else None

            if service_latitude is not None and not -90 <= service_latitude <= 90:
                raise ValueError("Invalid latitude")

            if service_longitude is not None and not -180 <= service_longitude <= 180:
                raise ValueError("Invalid longitude")

            if (service_latitude is None) != (service_longitude is None):
                flash("Please select a complete location on the map.")
                return redirect(url_for("manage_services"))

        except ValueError:
            flash("Please enter valid price and location details.")
            return redirect(url_for("manage_services"))

        try:
            connection = get_db_connection()
            cursor = connection.cursor()

            cursor.execute(
                """
                INSERT INTO services
                (
                    provider_id, service_name, category, price,
                    address, city, description, contact,
                    latitude, longitude,
                    is_active, is_verified
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, FALSE)
                """,
                (
                    session["provider_id"],
                    service_name,
                    category,
                    service_price,
                    address,
                    city,
                    description,
                    contact,
                    service_latitude,
                    service_longitude
                )
            )

            connection.commit()
            cursor.close()

            flash(
                "Service submitted successfully! "
                "It will appear publicly after verification."
            )
            return redirect(url_for("manage_services"))

        except Exception:
            import traceback
            traceback.print_exc()

            if connection:
                connection.rollback()

            flash("Unable to submit service. Please try again.")

        finally:
            if connection:
                connection.close()

    connection = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                service_name,
                category,
                price,
                city,
                is_verified,
                is_active
            FROM services
            WHERE provider_id = %s
            ORDER BY created_at DESC
            """,
            (session["provider_id"],)
        )

        services = cursor.fetchall()
        cursor.close()

        return render_template(
            "manage_services.html",
            services=services
        )
    except Exception:
       traceback.print_exc()
       flash("Unable to load your service listings.")
       return render_template(
        "manage_services.html",
        services=[]
    )

    finally:
        if connection:
            connection.close()

# ================= ADMIN ACCOUNT & AUTHENTICATION =================

def setup_admin_tables():
    """Create admin/settings tables and initialize the first admin from .env."""
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id SERIAL PRIMARY KEY,
                full_name VARCHAR(120) NOT NULL DEFAULT 'Administrator',
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                session_version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS site_information (
                id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                website_name VARCHAR(150) NOT NULL DEFAULT 'NewCity',
                description TEXT DEFAULT '',
                support_email VARCHAR(255) DEFAULT '',
                support_phone VARCHAR(30) DEFAULT '',
                service_area VARCHAR(255) DEFAULT '',
                about_text TEXT DEFAULT '',
                terms_text TEXT DEFAULT '',
                privacy_text TEXT DEFAULT ''
            )
        """)
        cursor.execute("""
            INSERT INTO site_information (id)
            VALUES (1)
            ON CONFLICT (id) DO NOTHING
        """)

        admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
        admin_password = os.getenv("ADMIN_PASSWORD", "")
        if admin_email and admin_password:
            cursor.execute("SELECT id FROM admins WHERE email = %s", (admin_email,))
            existing_admin = cursor.fetchone()
            if not existing_admin:
                cursor.execute("""
                    INSERT INTO admins (full_name, email, password_hash)
                    VALUES (%s, %s, %s)
                """, (
                    os.getenv("ADMIN_NAME", "Administrator"),
                    admin_email,
                    generate_password_hash(admin_password)
                ))
        # Admin-managed account status and categories/reports.
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
        cursor.execute("ALTER TABLE providers ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS service_categories (
                id SERIAL PRIMARY KEY,
                category_name VARCHAR(120) UNIQUE NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id SERIAL PRIMARY KEY,
                title VARCHAR(200) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status VARCHAR(30) NOT NULL DEFAULT 'Pending',
                reporter_email VARCHAR(255) DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.commit()
        cursor.close()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def admin_is_authenticated():
    """Validate admin session and session version against the database."""
    admin_id = session.get("admin_id")
    if not admin_id:
        return False

    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("""
            SELECT is_active, session_version
            FROM admins
            WHERE id = %s
        """, (admin_id,))
        admin = cursor.fetchone()
        cursor.close()

        if (
            not admin
            or not admin[0]
            or admin[1] != session.get("admin_session_version")
        ):
            session.clear()
            return False
        return True
    except Exception:
        traceback.print_exc()
        session.clear()
        return False
    finally:
        if connection:
            connection.close()


def admin_required(view_function):
    @wraps(view_function)
    def wrapped(*args, **kwargs):
        if not admin_is_authenticated():
            flash("Please log in as administrator.")
            return redirect(url_for("admin_login"))
        return view_function(*args, **kwargs)
    return wrapped


@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    try:
        setup_admin_tables()
    except Exception:
        traceback.print_exc()
        flash("Admin setup failed. Check your database and .env settings.")
        return render_template("admin_login.html")

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please enter admin email and password.")
            return redirect(url_for("admin_login"))

        connection = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            cursor.execute("""
                SELECT id, full_name, email, password_hash,
                       is_active, session_version
                FROM admins
                WHERE email = %s
            """, (email,))
            admin = cursor.fetchone()
            cursor.close()

            if (
                admin
                and admin[4]
                and check_password_hash(admin[3], password)
            ):
                session.clear()
                session["admin_id"] = admin[0]
                session["admin_name"] = admin[1]
                session["admin_email"] = admin[2]
                session["admin_session_version"] = admin[5]
                flash("Welcome to the admin panel.")
                return redirect(url_for("admin_dashboard"))

            flash("Invalid admin email or password.")
        except Exception:
            traceback.print_exc()
            flash("Admin login failed. Please try again.")
        finally:
            if connection:
                connection.close()

    return render_template("admin_login.html")


@app.route("/admin-logout")
def admin_logout():
    session.clear()
    flash("Admin logged out successfully.")
    return redirect(url_for("admin_login"))


# ================= ADMIN DASHBOARD =================

@app.route("/admin")
@admin_required
def admin_dashboard():
    connection = None
    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)
        cursor.execute("""
            SELECT (SELECT COUNT(*) FROM users) AS users,
                   (SELECT COUNT(*) FROM providers) AS providers,
                   (SELECT COUNT(*) FROM services) AS services,
                   (SELECT COUNT(*) FROM reports) AS reports
        """)
        stats = cursor.fetchone()
        cursor.execute("""
    SELECT id, reason AS title, description, status, created_at
    FROM reports
    ORDER BY created_at DESC
    LIMIT 8
""")
        reports = cursor.fetchall()
        cursor.execute("SELECT id, business_name, owner_name, email FROM providers WHERE is_verified = FALSE AND is_active = TRUE ORDER BY created_at DESC")
        verification_requests = cursor.fetchall()
        cursor.execute("""
            SELECT s.id, s.service_name, s.category, s.city, s.price,
                   p.business_name, p.owner_name
            FROM services s JOIN providers p ON p.id=s.provider_id
            WHERE s.is_verified=FALSE AND s.is_active=TRUE ORDER BY s.created_at DESC
        """)
        pending_services = cursor.fetchall()
        cursor.close()
        return render_template("admin_dashboard.html", stats=stats, reports=reports,
                               verification_requests=verification_requests, pending_services=pending_services)
    except Exception:
        traceback.print_exc()
        flash("Unable to load admin dashboard. Please check the database.")
        return redirect(url_for("admin_login"))
    finally:
        if connection: connection.close()


@app.route("/admin/users")
@admin_required
def admin_users():
    q = request.args.get("q", "").strip()
    connection = get_db_connection()
    try:
        cursor = get_dict_cursor(connection)
        cursor.execute("""SELECT id, first_name, last_name, email, mobile, city, is_active
                          FROM users WHERE (%s = '' OR first_name ILIKE %s OR last_name ILIKE %s
                          OR email ILIKE %s OR mobile ILIKE %s OR city ILIKE %s)
                          ORDER BY id DESC LIMIT 300""",
                       (q, f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"))
        rows = cursor.fetchall(); cursor.close()
        return render_template("admin_management.html", page="Users", rows=rows, query=q)
    finally: connection.close()


@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def admin_toggle_user(user_id):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("UPDATE users SET is_active = NOT is_active WHERE id = %s", (user_id,))
        connection.commit(); cursor.close()
        flash("User account status updated.")
    except Exception:
        connection.rollback(); traceback.print_exc(); flash("Unable to update user.")
    finally: connection.close()
    return redirect(url_for("admin_users"))

@app.route("/admin/providers")
@admin_required
def admin_providers():
    q = request.args.get("q", "").strip()
    connection = get_db_connection()
    try:
        cursor = get_dict_cursor(connection)
        cursor.execute("""SELECT id,business_name,owner_name,email,mobile,city,is_verified,is_active
                          FROM providers WHERE (%s='' OR business_name ILIKE %s OR owner_name ILIKE %s OR email ILIKE %s OR city ILIKE %s)
                          ORDER BY id DESC LIMIT 300""", (q,f"%{q}%",f"%{q}%",f"%{q}%",f"%{q}%"))
        rows=cursor.fetchall(); cursor.close()
        return render_template("admin_management.html",page="Providers",rows=rows,query=q)
    finally: connection.close()


@app.route("/admin/providers/<int:provider_id>/toggle", methods=["POST"])
@admin_required
def admin_toggle_provider(provider_id):
    connection=get_db_connection()
    try:
        cursor=connection.cursor(); cursor.execute("UPDATE providers SET is_active=NOT is_active WHERE id=%s",(provider_id,)); connection.commit(); cursor.close()
        flash("Provider account status updated.")
    except Exception:
        connection.rollback(); traceback.print_exc(); flash("Unable to update provider.")
    finally: connection.close()
    return redirect(url_for("admin_providers"))


@app.route("/admin/providers/<int:provider_id>/verify", methods=["POST"])
@admin_required
def admin_verify_provider(provider_id):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE providers SET is_verified = TRUE WHERE id = %s",
            (provider_id,)
        )
        connection.commit()
        cursor.close()
        flash("Provider verified successfully!")

    except Exception:
        connection.rollback()
        traceback.print_exc()
        flash("Unable to verify provider.")

    finally:
        connection.close()

    return redirect(url_for("admin_providers"))


@app.route("/admin/providers/<int:provider_id>/reject", methods=["POST"])
@admin_required
def admin_reject_provider(provider_id):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE providers SET is_verified = FALSE WHERE id = %s",
            (provider_id,)
        )
        connection.commit()
        cursor.close()
        flash("Provider verification rejected.")

    except Exception:
        connection.rollback()
        traceback.print_exc()
        flash("Unable to reject provider.")

    finally:
        connection.close()

    return redirect(url_for("admin_providers"))


@app.route("/admin/services")
@admin_required
def admin_services():
    q=request.args.get("q","").strip(); connection=get_db_connection()
    try:
        cursor=get_dict_cursor(connection)
        cursor.execute("""SELECT s.id,s.service_name,s.category,s.city,s.price,s.is_active,s.is_verified,p.business_name
                          FROM services s JOIN providers p ON p.id=s.provider_id
                          WHERE (%s='' OR s.service_name ILIKE %s OR s.category ILIKE %s OR s.city ILIKE %s OR p.business_name ILIKE %s)
                          ORDER BY s.id DESC LIMIT 300""",(q,f"%{q}%",f"%{q}%",f"%{q}%",f"%{q}%"))
        rows=cursor.fetchall(); cursor.close()
        return render_template("admin_management.html",page="Services",rows=rows,query=q)
    finally: connection.close()


@app.route("/admin/services/<int:service_id>/toggle",methods=["POST"])
@admin_required
def admin_toggle_service(service_id):
    connection=get_db_connection()
    try:
        cursor=connection.cursor(); cursor.execute("UPDATE services SET is_active=NOT is_active WHERE id=%s",(service_id,)); connection.commit(); cursor.close(); flash("Service visibility updated.")
    except Exception:
        connection.rollback(); traceback.print_exc(); flash("Unable to update service.")
    finally: connection.close()
    return redirect(url_for("admin_services"))


@app.route("/admin/categories",methods=["GET","POST"])
@admin_required
def admin_categories():
    connection=get_db_connection()
    try:
        cursor=get_dict_cursor(connection)
        if request.method=="POST":
            name=request.form.get("category_name","").strip()
            if not name: flash("Category name is required.")
            else:
                try:
                    cursor.execute("INSERT INTO service_categories(category_name,is_active) VALUES(%s,TRUE)",(name,)); connection.commit(); flash("Category added.")
                except Exception:
                    connection.rollback(); flash("Category already exists or could not be added.")
            return redirect(url_for("admin_categories"))
        cursor.execute("SELECT category_name,is_active FROM service_categories ORDER BY category_name")
        rows=cursor.fetchall(); cursor.close()
        return render_template("admin_management.html",page="Categories",rows=rows,query="")
    finally: connection.close()


@app.route("/admin/categories/<path:category_name>/toggle",methods=["POST"])
@admin_required
def admin_toggle_category(category_name):
    connection=get_db_connection()
    try:
        cursor=connection.cursor(); cursor.execute("UPDATE service_categories SET is_active=NOT is_active WHERE category_name=%s",(category_name,)); connection.commit(); cursor.close(); flash("Category status updated.")
    except Exception:
        connection.rollback(); traceback.print_exc(); flash("Unable to update category.")
    finally: connection.close()
    return redirect(url_for("admin_categories"))


@app.route("/admin/reports")
@admin_required
def admin_reports():
    connection = get_db_connection()
    try:
        cursor = get_dict_cursor(connection)
        cursor.execute("""
            SELECT id, reason AS title, description, status, created_at
            FROM reports
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        cursor.close()

        return render_template(
            "admin_management.html",
            page="Reports",
            rows=rows,
            query=""
        )
    finally:
        connection.close()

@app.route("/admin/reports/<int:report_id>/status",methods=["POST"])
@admin_required
def admin_report_status(report_id):
    status=request.form.get("status","")
    if status not in ("Pending","Under Review","Resolved"): abort(400)
    connection=get_db_connection()
    try:
        cursor=connection.cursor(); cursor.execute("UPDATE reports SET status=%s WHERE id=%s",(status,report_id)); connection.commit(); cursor.close(); flash("Report status updated.")
    except Exception:
        connection.rollback(); traceback.print_exc(); flash("Unable to update report.")
    finally: connection.close()
    return redirect(url_for("admin_reports"))


@app.route("/admin/approve-provider/<int:provider_id>", methods=["POST"])
@admin_required
def approve_provider(provider_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE providers SET is_verified = TRUE WHERE id = %s",
            (provider_id,)
        )
        connection.commit()
        cursor.close()
        flash("Provider approved successfully.")
    except Exception:
        traceback.print_exc()
        if connection:
            connection.rollback()
        flash("Unable to approve provider.")
    finally:
        if connection:
            connection.close()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/approve-service/<int:service_id>", methods=["POST"])
@admin_required
def approve_service(service_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("""
            UPDATE services
            SET is_verified = TRUE, is_active = TRUE
            WHERE id = %s
        """, (service_id,))
        connection.commit()
        cursor.close()
        flash("Service approved successfully.")
    except Exception:
        traceback.print_exc()
        if connection:
            connection.rollback()
        flash("Unable to approve service.")
    finally:
        if connection:
            connection.close()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/reject-service/<int:service_id>", methods=["POST"])
@admin_required
def reject_service(service_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("""
            UPDATE services
            SET is_verified = FALSE, is_active = FALSE
            WHERE id = %s
        """, (service_id,))
        connection.commit()
        cursor.close()
        flash("Service rejected. It has not been deleted.")
    except Exception:
        traceback.print_exc()
        if connection:
            connection.rollback()
        flash("Unable to reject service.")
    finally:
        if connection:
            connection.close()
    return redirect(url_for("admin_dashboard"))


# ================= ADMIN SETTINGS =================

@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    connection = None
    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)

        if request.method == "POST":
            form_action = request.form.get("form_action", "")

            if form_action == "profile":
                full_name = request.form.get("full_name", "").strip()
                if not full_name:
                    flash("Admin name is required.")
                    return redirect(url_for("admin_settings"))

                cursor.execute("""
                    UPDATE admins SET full_name = %s WHERE id = %s
                """, (full_name, session["admin_id"]))
                connection.commit()
                session["admin_name"] = full_name
                flash("Admin profile updated successfully.")

            elif form_action == "account":
                new_email = request.form.get("email", "").strip().lower()
                if not new_email:
                    flash("Email address is required.")
                    return redirect(url_for("admin_settings"))

                cursor.execute("""
                    UPDATE admins SET email = %s WHERE id = %s
                """, (new_email, session["admin_id"]))
                connection.commit()
                session["admin_email"] = new_email
                flash("Admin email updated successfully.")

            elif form_action == "logout_all":
                cursor.execute("""
                    UPDATE admins
                    SET session_version = session_version + 1
                    WHERE id = %s
                """, (session["admin_id"],))
                connection.commit()
                session.clear()
                flash("All admin sessions have been logged out.")
                return redirect(url_for("admin_login"))

            elif form_action == "deactivate":
                cursor.execute("""
                    UPDATE admins
                    SET is_active = FALSE,
                        session_version = session_version + 1
                    WHERE id = %s
                """, (session["admin_id"],))
                connection.commit()
                session.clear()
                flash("Admin account deactivated.")
                return redirect(url_for("admin_login"))

            elif form_action == "website":
                website_name = request.form.get("website_name", "").strip()
                description = request.form.get("description", "").strip()
                support_email = request.form.get("support_email", "").strip()
                support_phone = request.form.get("support_phone", "").strip()
                service_area = request.form.get("service_area", "").strip()
                about_text = request.form.get("about_text", "").strip()
                terms_text = request.form.get("terms_text", "").strip()
                privacy_text = request.form.get("privacy_text", "").strip()

                if not website_name:
                    flash("Website name is required.")
                    return redirect(url_for("admin_settings"))

                cursor.execute("""
                    UPDATE site_information
                    SET website_name = %s,
                        description = %s,
                        support_email = %s,
                        support_phone = %s,
                        service_area = %s,
                        about_text = %s,
                        terms_text = %s,
                        privacy_text = %s
                    WHERE id = 1
                """, (
                    website_name, description, support_email, support_phone,
                    service_area, about_text, terms_text, privacy_text
                ))
                connection.commit()
                flash("Website information saved successfully.")

            return redirect(url_for("admin_settings"))

        cursor.execute("""
            SELECT full_name, email
            FROM admins
            WHERE id = %s
        """, (session["admin_id"],))
        admin = cursor.fetchone()

        cursor.execute("""
            SELECT website_name, description, support_email, support_phone,
                   service_area, about_text, terms_text, privacy_text
            FROM site_information
            WHERE id = 1
        """)
        site_info = cursor.fetchone()
        cursor.close()

        return render_template(
            "admin_settings.html",
            admin=admin,
            site_info=site_info
        )
    except Exception:
        traceback.print_exc()
        if connection:
            connection.rollback()
        flash("Unable to load admin settings.")
        return redirect(url_for("admin_dashboard"))
    finally:
        if connection:
            connection.close()


@app.route("/admin/change-password", methods=["GET", "POST"])
@admin_required
def admin_change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not all([current_password, new_password, confirm_password]):
            flash("Please fill all password fields.")
            return redirect(url_for("admin_change_password"))

        if len(new_password) < 8:
            flash("New password must contain at least 8 characters.")
            return redirect(url_for("admin_change_password"))

        if new_password != confirm_password:
            flash("New passwords do not match.")
            return redirect(url_for("admin_change_password"))

        connection = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            cursor.execute("""
                SELECT password_hash FROM admins WHERE id = %s
            """, (session["admin_id"],))
            admin = cursor.fetchone()

            if not admin or not check_password_hash(admin[0], current_password):
                flash("Current password is incorrect.")
                return redirect(url_for("admin_change_password"))

            cursor.execute("""
                UPDATE admins
                SET password_hash = %s,
                    session_version = session_version + 1
                WHERE id = %s
            """, (
                generate_password_hash(new_password),
                session["admin_id"]
            ))
            connection.commit()
            cursor.close()
            session.clear()
            flash("Password changed. Please log in again.")
            return redirect(url_for("admin_login"))
        except Exception:
            traceback.print_exc()
            if connection:
                connection.rollback()
            flash("Unable to change admin password.")
        finally:
            if connection:
                connection.close()

    return render_template("admin_change_password.html")

# ================= FAVOURITES TABLE SETUP =================

def setup_favourites_table():
    connection = get_db_connection()
    cursor = None

    try:
        cursor = connection.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS favourites (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS favourites_user_service_unique
            ON favourites (user_id, service_id)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS service_bookings (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
                service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
                customer_name VARCHAR(150) NOT NULL,
                customer_mobile VARCHAR(30) NOT NULL,
                message TEXT,
                status VARCHAR(20) NOT NULL DEFAULT 'Pending'
                    CHECK (status IN ('Pending', 'Accepted', 'Rejected', 'Completed')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        connection.commit()

    except Exception:
        connection.rollback()
        traceback.print_exc()
        raise

    finally:
        if cursor:
            cursor.close()
        connection.close()


# ================= EDIT PROVIDER SERVICE =================

@app.route("/edit-service/<int:service_id>", methods=["GET", "POST"])
def edit_service(service_id):
    if not login_required("provider"):
        return redirect(url_for("login", role="provider"))

    connection = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        if request.method == "POST":
            service_name = request.form.get("service_name", "").strip()
            category = request.form.get("category", "").strip()
            price = request.form.get("price", "").strip()
            address = request.form.get("address", "").strip()
            city = request.form.get("city", "").strip()
            description = request.form.get("description", "").strip()
            contact = request.form.get("contact", "").strip()

            if not all([service_name, category, address, city, description]):
                flash("Please fill all required fields.")
                return redirect(url_for("edit_service", service_id=service_id))

            try:
                service_price = float(price) if price else None
                if service_price is not None and service_price < 0:
                    flash("Price cannot be negative.")
                    return redirect(url_for("edit_service", service_id=service_id))
            except ValueError:
                flash("Please enter a valid price.")
                return redirect(url_for("edit_service", service_id=service_id))

            cursor.execute("""
                UPDATE services
                SET service_name = %s,
                    category = %s,
                    price = %s,
                    address = %s,
                    city = %s,
                    description = %s,
                    contact = %s,
                    is_verified = FALSE
                WHERE id = %s AND provider_id = %s
            """, (
                service_name, category, service_price,
                address, city, description, contact,
                service_id, session["provider_id"]
            ))

            if cursor.rowcount == 0:
                connection.rollback()
                flash("Service not found.")
            else:
                connection.commit()
                flash("Service updated! It will be visible after verification.")

            return redirect(url_for("manage_services"))

        cursor.execute("""
            SELECT id, service_name, category, price,
                   address, city, description, contact
            FROM services
            WHERE id = %s AND provider_id = %s
        """, (service_id, session["provider_id"]))

        service = cursor.fetchone()

        if not service:
            abort(404)

        return render_template("edit_service.html", service=service)

    except Exception:
        traceback.print_exc()
        if connection:
            connection.rollback()
        flash("Unable to edit service. Please try again.")
        return redirect(url_for("manage_services"))

    finally:
        if connection:
            connection.close()


# ================= DELETE PROVIDER SERVICE =================

@app.route("/delete-service/<int:service_id>", methods=["POST"])
def delete_service(service_id):
    if not login_required("provider"):
        return redirect(url_for("login", role="provider"))

    connection = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("""
            DELETE FROM services
            WHERE id = %s AND provider_id = %s
        """, (service_id, session["provider_id"]))

        if cursor.rowcount == 0:
            connection.rollback()
            flash("Service not found.")
        else:
            connection.commit()
            flash("Service deleted successfully!")

        cursor.close()

    except Exception:
        traceback.print_exc()
        if connection:
            connection.rollback()
        flash("Unable to delete service. Please try again.")

    finally:
        if connection:
            connection.close()

    return redirect(url_for("manage_services"))


# ================= CUSTOMER SERVICE REQUEST =================
@app.route("/service-details/<int:service_id>/request", methods=["POST"])
def send_service_request(service_id):
    if not login_required("user"):
        return redirect(url_for("login", role="user"))

    customer_name = request.form.get("customer_name", "").strip()
    customer_mobile = request.form.get("customer_mobile", "").strip()
    message = request.form.get("message", "").strip()

    if not customer_name or not customer_mobile:
        flash("Please enter your name and mobile number.")
        return redirect(url_for("service_details", service_id=service_id))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("""
            SELECT provider_id FROM services
            WHERE id = %s AND is_active = TRUE AND is_verified = TRUE
        """, (service_id,))
        service = cursor.fetchone()
        if not service:
            abort(404)

        cursor.execute("""
            INSERT INTO service_bookings
                (user_id, provider_id, service_id, customer_name, customer_mobile, message)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (session["user_id"], service[0], service_id,
              customer_name, customer_mobile, message))
        connection.commit()
        flash("Your service request has been sent successfully!")
    except Exception:
        if connection:
            connection.rollback()
        traceback.print_exc()
        flash("Unable to send request right now. Please try again.")
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
    return redirect(url_for("service_details", service_id=service_id))


# ================= PROVIDER BOOKINGS / LEADS =================
@app.route("/provider-bookings")
def provider_bookings():
    if not login_required("provider"):
        return redirect(url_for("login", role="provider"))
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)
        cursor.execute("""
            SELECT b.id, b.customer_name, b.customer_mobile, b.message,
                   b.status, b.created_at, s.service_name, s.category
            FROM service_bookings b
            JOIN services s ON s.id = b.service_id
            WHERE b.provider_id = %s
            ORDER BY b.created_at DESC
        """, (session["provider_id"],))
        bookings = cursor.fetchall()
        return render_template("provider_bookings.html", bookings=bookings)
    except Exception:
        traceback.print_exc()
        flash("Unable to load bookings. Please try again.")
        return render_template("provider_bookings.html", bookings=[])
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route("/provider-bookings/<int:booking_id>/<string:action>", methods=["POST"])
def update_provider_booking(booking_id, action):
    if not login_required("provider"):
        return redirect(url_for("login", role="provider"))
    status_map = {"accept": "Accepted", "reject": "Rejected", "complete": "Completed"}
    new_status = status_map.get(action)
    if not new_status:
        abort(400)
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("""
            UPDATE service_bookings SET status = %s
            WHERE id = %s AND provider_id = %s
        """, (new_status, booking_id, session["provider_id"]))
        if cursor.rowcount:
            connection.commit()
            flash(f"Request marked as {new_status}.")
        else:
            connection.rollback()
            flash("Booking not found.")
    except Exception:
        if connection:
            connection.rollback()
        traceback.print_exc()
        flash("Unable to update request.")
    finally:
        if connection:
            connection.close()
    return redirect(url_for("provider_bookings"))


# ================= PROVIDER REVIEWS =================
@app.route("/provider-reviews")
def provider_reviews():
    if not login_required("provider"):
        return redirect(url_for("login", role="provider"))
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)
        cursor.execute("""
            SELECT r.rating, r.comment, r.created_at, s.service_name,
                   u.first_name, u.last_name
            FROM reviews r
            JOIN services s ON s.id = r.service_id
            JOIN users u ON u.id = r.user_id
            WHERE s.provider_id = %s
            ORDER BY r.created_at DESC
        """, (session["provider_id"],))
        reviews = cursor.fetchall()
        return render_template("provider_reviews.html", reviews=reviews)
    except Exception:
        traceback.print_exc()
        flash("Unable to load provider reviews.")
        return render_template("provider_reviews.html", reviews=[])
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# ================= PROVIDER EARNINGS =================
@app.route("/provider-earnings")
def provider_earnings():
    if not login_required("provider"):
        return redirect(url_for("login", role="provider"))
    return render_template("provider_earnings.html")


# ================= PROVIDER PROFILE =================
@app.route("/provider-profile", methods=["GET", "POST"])
def provider_profile():
    if not login_required("provider"):
        return redirect(url_for("login", role="provider"))
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_dict_cursor(connection)
        if request.method == "POST":
            business_name = request.form.get("business_name", "").strip()
            owner_name = request.form.get("owner_name", "").strip()
            mobile = request.form.get("mobile", "").strip()
            category = request.form.get("category", "").strip()
            address = request.form.get("address", "").strip()
            city = request.form.get("city", "").strip()
            description = request.form.get("description", "").strip()
            if not business_name or not owner_name or not mobile:
                flash("Business name, owner name and mobile are required.")
            else:
                cursor.execute("""
                    UPDATE providers
                    SET business_name=%s, owner_name=%s, mobile=%s,
                        category=%s, address=%s, city=%s, description=%s
                    WHERE id=%s
                """, (business_name, owner_name, mobile, category,
                      address, city, description, session["provider_id"]))
                connection.commit()
                session["provider_name"] = business_name
                flash("Provider profile updated successfully.")
                return redirect(url_for("provider_profile"))
        cursor.execute("""
            SELECT business_name, owner_name, email, mobile, category,
                   address, city, description, is_verified
            FROM providers WHERE id=%s
        """, (session["provider_id"],))
        provider = cursor.fetchone()
        if not provider:
            abort(404)
        return render_template("provider_profile.html", provider=provider)
    except Exception:
        if connection:
            connection.rollback()
        traceback.print_exc()
        flash("Unable to load provider profile.")
        return redirect(url_for("provider_dashboard"))
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# ================= RUN APPLICATION =================

if __name__ == "__main__":
    setup_favourites_table()
    setup_admin_tables()
    app.run(debug=True, use_reloader=False)

