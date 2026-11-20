# Calorie & Macro Tracker

A mobile-first, full-stack web application for tracking daily calories, macronutrients, and body weight, powered by **Google Gemini AI** for natural-language food lookup and personalized BMR/TDEE calorie recommendations, complete with mutual friends progress sharing and account-synced theme customization.

---

## Key Features

* **Daily Calorie & Macronutrient Tracking:**
  * Monitor daily calorie intake against your target goal (`kcal left` / `kcal over` indicator) alongside **Protein, Carbs, Fat, and Fiber** with individual progress bars.
  * Categorize meals into **Breakfast, Lunch, Dinner, and Snack**.
  * Browse historical logs using quick day navigation or an **interactive calendar** with dot indicators for days that have logged meals.
* **Hybrid AI Food Search (SQLite + Google Gemini):**
  * Instant search against a local SQLite food cache (`alimente_locale`), automatically supplemented by **Gemini** (`gemini-3.5-flash-lite`) with structured Pydantic outputs (`SuggestionList`) for foods queried in English or Romanian.
  * Automatically caches AI-discovered foods locally for faster future lookups.
  * Switch seamlessly between **serving count** and **custom weight/volume (`g` / `ml`)** both when adding foods and inline within the daily log.
* **User Profile, Body Weight Log & AI Calorie Advice:**
  * Track body weight over time with automatic calculation of weight change from your initial entry.
  * Dynamically computes daily protein, carb, fat, and fiber targets based on your calorie goal and latest logged weight.
  * **AI Calorie Advice:** calculates your exact BMR and TDEE using the **Mifflin-St Jeor** equation and provides one-click targets for **Moderate Fat Loss**, **Weight Maintenance**, and **Lean Muscle Gain**.
* **Mutual Friends & Progress Sharing:**
  * Send friend requests by username; once mutual, view each other's daily calorie progress, macro breakdown, and meal categories for any selected date.
* **Authentication & Account-Synced Appearance:**
  * User registration and login with PBKDF2/scrypt password hashing (`werkzeug.security`) and 30-day persistent sessions.
  * **Light Mode / Dark Mode** (available both in Settings and directly on the Sign In / Sign Up screen) and **Accent Color** customization (6 presets + custom hex color picker), automatically synced to your user profile in SQLite.
* **iOS Web App Ready:**
  * Configured with Apple touch icons (`static/icon.png`) and mobile web app meta tags so it can be added directly to an iPhone Home Screen,.

---

## Tech Stack

* **Backend:** Python 3, Flask 3.1, Werkzeug, Python-Dotenv
* **Database:** SQLite (`sqlite3`) with automatic schema initialization and column migrations
* **AI & Structured Outputs:** Google GenAI SDK (`google-genai`, `gemini-3.5-flash-lite`), Pydantic
* **Frontend:** HTML5 (Jinja2 templates), Tailwind CSS (CDN), Vanilla JavaScript (Fetch API)

---

## Project Structure

```text
├── app.py                 # Flask application, SQLite models/migrations, and REST API endpoints
├── templates/
│   └── index.html         # Single-page UI (Auth, Tracker, Friends, Profile, Appearance Settings)
├── static/
│   └── icon.png           # App icon & Apple Home Screen touch icon
├── requirements.txt       # Python dependencies
├── calories.db            # SQLite database (auto-created on first run, git-ignored)
├── .env                   # Environment secrets (GEMINI_API_KEY, FLASK_SECRET_KEY)
└── README.md              # Project documentation
```

## Installation & Local Setup

Create and activate a virtual environment (optional, recommended):

Linux/MacOS
````text
python -m venv venv
source venv/bin/activate
````

Windows
````text
python -m venv venv
venv\Scripts\activate
````

Install dependencies:

Bash
````text
pip install -r requirements.txt
````

Create a .env file in the root directory (listed in .gitignore so your keys stay private):

```text
GEMINI_API_KEY=your_gemini_api_key
```

Run the server:

Bash
```text
python app.py
```

The app will be accessible at http://127.0.0.1:5000 (or via your local Wi-Fi IP on port 5000 for mobile access).

