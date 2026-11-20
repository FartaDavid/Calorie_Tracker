import os
import re
import json
import sqlite3
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from google.genai import types
from pydantic import BaseModel

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "cheie-secreta-super-sigura-calorii-2026")
app.permanent_session_lifetime = timedelta(days=30)

load_dotenv(override=True)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

VALID_MEAL_TYPES = {'breakfast', 'lunch', 'dinner', 'snack'}

# 1. Database initialization + automatic migration
def init_db():
    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            daily_goal INTEGER NOT NULL DEFAULT 2000,
            height_cm REAL DEFAULT NULL,
            birth_date TEXT DEFAULT NULL,
            gender TEXT DEFAULT 'unspecified',
            activity_level TEXT DEFAULT 'moderate',
            theme_mode TEXT NOT NULL DEFAULT 'light',
            accent_color TEXT NOT NULL DEFAULT '#4f46e5'
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT NOT NULL,
            aliment TEXT NOT NULL,
            calories INTEGER NOT NULL,
            protein REAL NOT NULL DEFAULT 0,
            fat REAL NOT NULL DEFAULT 0,
            carbs REAL NOT NULL DEFAULT 0,
            fiber REAL NOT NULL DEFAULT 0,
            portions REAL NOT NULL DEFAULT 1,
            unit_kcal INTEGER NOT NULL DEFAULT 0,
            unit_protein REAL NOT NULL DEFAULT 0,
            unit_fat REAL NOT NULL DEFAULT 0,
            unit_carbs REAL NOT NULL DEFAULT 0,
            unit_fiber REAL NOT NULL DEFAULT 0,
            base_name TEXT NOT NULL DEFAULT '',
            base_portion TEXT NOT NULL DEFAULT '1 serving',
            measure_mode TEXT NOT NULL DEFAULT 'serving',
            custom_weight REAL NOT NULL DEFAULT 0,
            weight_unit TEXT NOT NULL DEFAULT 'g',
            meal_type TEXT NOT NULL DEFAULT 'breakfast',
            data TEXT NOT NULL
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS alimente_locale (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nume TEXT UNIQUE NOT NULL,
            portie TEXT NOT NULL,
            kcal_portie INTEGER NOT NULL,
            protein REAL NOT NULL DEFAULT 0,
            fat REAL NOT NULL DEFAULT 0,
            carbs REAL NOT NULL DEFAULT 0,
            fiber REAL NOT NULL DEFAULT 0
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS weight_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT NOT NULL,
            weight_kg REAL NOT NULL,
            data TEXT NOT NULL,
            UNIQUE(user, data)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS friendships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_from TEXT NOT NULL,
            user_to TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_from, user_to)
        )
    ''')

    # Safe migrations for existing databases
    c.execute("PRAGMA table_info(users)")
    user_cols = [row[1] for row in c.fetchall()]
    new_user_cols = {
        'height_cm': 'REAL DEFAULT NULL',
        'birth_date': 'TEXT DEFAULT NULL',
        'gender': "TEXT DEFAULT 'unspecified'",
        'activity_level': "TEXT DEFAULT 'moderate'",
        'theme_mode': "TEXT NOT NULL DEFAULT 'light'",
        'accent_color': "TEXT NOT NULL DEFAULT '#4f46e5'"
    }
    for col, col_type in new_user_cols.items():
        if col not in user_cols:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")

    c.execute("PRAGMA table_info(meals)")
    meal_cols = [row[1] for row in c.fetchall()]
    new_meal_cols = {
        'protein': 'REAL NOT NULL DEFAULT 0',
        'fat': 'REAL NOT NULL DEFAULT 0',
        'carbs': 'REAL NOT NULL DEFAULT 0',
        'fiber': 'REAL NOT NULL DEFAULT 0',
        'portions': 'REAL NOT NULL DEFAULT 1',
        'unit_kcal': 'INTEGER NOT NULL DEFAULT 0',
        'unit_protein': 'REAL NOT NULL DEFAULT 0',
        'unit_fat': 'REAL NOT NULL DEFAULT 0',
        'unit_carbs': 'REAL NOT NULL DEFAULT 0',
        'unit_fiber': 'REAL NOT NULL DEFAULT 0',
        'base_name': "TEXT NOT NULL DEFAULT ''",
        'base_portion': "TEXT NOT NULL DEFAULT '1 serving'",
        'measure_mode': "TEXT NOT NULL DEFAULT 'serving'",
        'custom_weight': 'REAL NOT NULL DEFAULT 0',
        'weight_unit': "TEXT NOT NULL DEFAULT 'g'",
        'meal_type': "TEXT NOT NULL DEFAULT 'breakfast'"
    }
    for col, col_type in new_meal_cols.items():
        if col not in meal_cols:
            c.execute(f"ALTER TABLE meals ADD COLUMN {col} {col_type}")

    c.execute("PRAGMA table_info(alimente_locale)")
    local_cols = [row[1] for row in c.fetchall()]
    for col in ['protein', 'fat', 'carbs', 'fiber']:
        if col not in local_cols:
            c.execute(f"ALTER TABLE alimente_locale ADD COLUMN {col} REAL NOT NULL DEFAULT 0")
    
    c.execute('SELECT COUNT(*) FROM alimente_locale')
    if c.fetchone()[0] == 0:
        initial_foods = [
            ('Medium Banana', '1 piece (~120g)', 105, 1.3, 0.4, 27.0, 3.1),
            ('Boiled / Fried Egg', '1 large (~60g)', 78, 6.3, 5.3, 0.6, 0.0),
            ('Medium Apple', '1 piece (~150g)', 80, 0.4, 0.3, 21.0, 3.6),
            ('Whole Wheat Bread Slice', '1 slice (~35g)', 85, 3.6, 1.1, 15.0, 2.4),
            ('Grilled Chicken Breast', '1 portion (~150g)', 248, 46.5, 5.4, 0.0, 0.0),
            ('Coffee with Milk', '1 cup (~200ml)', 60, 3.2, 3.3, 4.8, 0.0),
            ('Sesame Bagel / Pretzel', '1 piece (~100g)', 280, 9.5, 3.0, 54.0, 2.5)
        ]
        c.executemany(
            '''INSERT INTO alimente_locale 
               (nume, portie, kcal_portie, protein, fat, carbs, fiber) 
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            initial_foods
        )
        
    conn.commit()
    conn.close()

init_db()

# Helper: Compute clean daily macro targets based on calorie goal & latest weight
def compute_macro_targets(daily_goal, weight_kg=None):
    if weight_kg and weight_kg > 0:
        protein_g = int(round((weight_kg * 1.8) / 5.0) * 5)
        max_prot = int((daily_goal * 0.40) / 4)
        protein_g = min(protein_g, max_prot)
    else:
        protein_g = int(round(((daily_goal * 0.25) / 4.0) / 5.0) * 5)

    fat_g = int(round(((daily_goal * 0.25) / 9.0) / 5.0) * 5)
    remaining_kcal = max(0, daily_goal - (protein_g * 4) - (fat_g * 9))
    carbs_g = int(round((remaining_kcal / 4.0) / 5.0) * 5)
    fiber_g = max(20, int(round((daily_goal / 1000.0) * 14)))

    return {
        'protein': protein_g,
        'carbs': carbs_g,
        'fat': fat_g,
        'fiber': fiber_g
    }

def get_user_latest_weight(cursor, username):
    cursor.execute(
        'SELECT weight_kg FROM weight_logs WHERE user = ? ORDER BY data DESC LIMIT 1',
        (username,)
    )
    row = cursor.fetchone()
    return row[0] if row else None

# 2. Home route
@app.route('/')
def acasa():
    if 'username' in session:
        conn = sqlite3.connect('calories.db')
        c = conn.cursor()
        c.execute(
            'SELECT daily_goal, theme_mode, accent_color FROM users WHERE username = ?',
            (session['username'],)
        )
        row = c.fetchone()
        goal = row[0] if row else session.get('daily_goal', 2000)
        theme_mode = (row[1] if row and row[1] in ('light', 'dark') else 'light')
        accent_color = (row[2] if row and row[2] else '#4f46e5')
        latest_w = get_user_latest_weight(c, session['username'])
        conn.close()

        session['daily_goal'] = goal
        macros = compute_macro_targets(goal, latest_w)

        return render_template(
            'index.html',
            logged_in=True,
            username=session['username'],
            daily_goal=goal,
            macro_targets=macros,
            theme_mode=theme_mode,
            accent_color=accent_color
        )
    return render_template(
        'index.html',
        logged_in=False,
        username='',
        daily_goal=2000,
        macro_targets={'protein': 125, 'carbs': 250, 'fat': 55, 'fiber': 28},
        theme_mode='light',
        accent_color='#4f46e5'
    )

# 3. Auth routes
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username', '').strip().lower()
    password = data.get('password', '').strip()
    daily_goal = int(data.get('daily_goal') or 2000)
    theme_mode = data.get('theme_mode', 'light')
    if theme_mode not in ('light', 'dark'):
        theme_mode = 'light'

    if len(username) < 3 or len(password) < 4:
        return jsonify({'status': 'error', 'message': 'Username must be at least 3 characters and password at least 4.'}), 400

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    try:
        pw_hash = generate_password_hash(password)
        c.execute(
            'INSERT INTO users (username, password_hash, daily_goal, theme_mode, accent_color) VALUES (?, ?, ?, ?, ?)',
            (username, pw_hash, daily_goal, theme_mode, '#4f46e5')
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Username already exists. Choose another one or sign in.'}), 400
    conn.close()

    session.permanent = True
    session['username'] = username
    session['daily_goal'] = daily_goal
    return jsonify({'status': 'succes'})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username', '').strip().lower()
    password = data.get('password', '').strip()
    theme_mode = (data.get('theme_mode') or '').strip().lower()

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute('SELECT password_hash, daily_goal FROM users WHERE username = ?', (username,))
    row = c.fetchone()

    if not row or not check_password_hash(row[0], password):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Invalid username or password.'}), 401

    if theme_mode in ('light', 'dark'):
        c.execute('UPDATE users SET theme_mode = ? WHERE username = ?', (theme_mode, username))
        conn.commit()

    conn.close()

    session.permanent = True
    session['username'] = username
    session['daily_goal'] = row[1]
    return jsonify({'status': 'succes'})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('acasa'))

# Save account appearance settings (theme_mode + accent_color)
@app.route('/api/settings', methods=['POST'])
def update_user_settings():
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    data = request.json or {}
    theme_mode = (data.get('theme_mode') or 'light').strip().lower()
    accent_color = (data.get('accent_color') or '#4f46e5').strip().lower()

    if theme_mode not in ('light', 'dark'):
        theme_mode = 'light'
    if not re.match(r'^#[0-9a-f]{6}$', accent_color):
        accent_color = '#4f46e5'

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute(
        'UPDATE users SET theme_mode = ?, accent_color = ? WHERE username = ?',
        (theme_mode, accent_color, session['username'])
    )
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'succes',
        'theme_mode': theme_mode,
        'accent_color': accent_color
    })

# 4. Hybrid Search
class FoodSuggestion(BaseModel):
    name: str
    portion: str
    kcal: int
    protein: float
    fat: float
    carbs: float
    fiber: float

class SuggestionList(BaseModel):
    results: list[FoodSuggestion]

@app.route('/api/search')
def search_aliment():
    if 'username' not in session:
        return jsonify([]), 401

    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return jsonify([])

    final_results = []

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute(
        '''SELECT nume, portie, kcal_portie, protein, fat, carbs, fiber 
           FROM alimente_locale WHERE nume LIKE ? LIMIT 4''',
        (f'%{query}%',)
    )
    for r in c.fetchall():
        final_results.append({
            'name': r[0],
            'portion': f"⭐ {r[1]}",
            'kcal': r[2],
            'protein': round(r[3], 1),
            'fat': round(r[4], 1),
            'carbs': round(r[5], 1),
            'fiber': round(r[6], 1)
        })
    conn.close()

    prompt = f"""
    You are an expert nutritionist. The user searches for: "{query}" (the query may be in English or Romanian).
    Provide 1 to 4 relevant food items in English (keep local brand names if applicable, e.g., "Zuzu Greek Yogurt 10%").
    Strict rules:
    - 'name': clean food name in English (e.g., "Whole Milk", "Medium Banana", "Espresso").
    - 'portion': natural serving unit in English WITH weight/volume in parentheses.
      * FOR DRINKS / LIQUIDS (milk, juice, coffee, tea, soda, broth, etc.), ALWAYS use 'ml' (e.g., "1 cup (~240ml)", "1 glass (~250ml)", "100ml").
      * FOR SOLID FOODS, ALWAYS use 'g' (e.g., "1 piece (~120g)", "1 slice (~35g)", "100g").
    - 'kcal': exact calories FOR THAT PORTION.
    - 'protein', 'fat', 'carbs', 'fiber': grams FOR THAT EXACT PORTION (rounded to 1 decimal).
    """

    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SuggestionList,
                temperature=0.2
            ),
        )
        ai_data = json.loads(response.text)
        for item in ai_data.get('results', []):
            if not any(r['name'].lower() == item['name'].lower() for r in final_results):
                final_results.append({
                    'name': item['name'].replace("'", "").replace('"', ''),
                    'portion': item['portion'].replace("'", "").replace('"', ''),
                    'kcal': item['kcal'],
                    'protein': round(item['protein'], 1),
                    'fat': round(item['fat'], 1),
                    'carbs': round(item['carbs'], 1),
                    'fiber': round(item['fiber'], 1)
                })
    except Exception as e:
        print(f"--> AI Error: {e}")

    return jsonify(final_results)

# 5. Add meal for the selected date & meal category
@app.route('/api/add', methods=['POST'])
def add_meal():
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    data = request.json
    target_date = data.get('date') or str(date.today())
    current_user = session['username']
    meal_type = (data.get('meal_type') or 'breakfast').strip().lower()
    if meal_type not in VALID_MEAL_TYPES:
        meal_type = 'breakfast'
    
    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    
    c.execute(
        '''INSERT INTO meals (
               user, aliment, calories, protein, fat, carbs, fiber,
               portions, unit_kcal, unit_protein, unit_fat, unit_carbs, unit_fiber,
               base_name, base_portion, measure_mode, custom_weight, weight_unit, meal_type, data
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            current_user,
            data['food'],
            data['calories'],
            data.get('protein', 0),
            data.get('fat', 0),
            data.get('carbs', 0),
            data.get('fiber', 0),
            data.get('portions', 1),
            data.get('unit_kcal', 0),
            data.get('unit_protein', 0),
            data.get('unit_fat', 0),
            data.get('unit_carbs', 0),
            data.get('unit_fiber', 0),
            data.get('base_name', data['food']),
            data.get('base_portion', '1 serving'),
            data.get('measure_mode', 'serving'),
            data.get('custom_weight', 0),
            data.get('weight_unit', 'g'),
            meal_type,
            target_date
        )
    )
    
    if data.get('base_name') and data.get('unit_kcal'):
        c.execute(
            '''INSERT OR IGNORE INTO alimente_locale 
               (nume, portie, kcal_portie, protein, fat, carbs, fiber) 
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (
                data['base_name'],
                data.get('base_portion', '1 serving'),
                data['unit_kcal'],
                data.get('unit_protein', 0),
                data.get('unit_fat', 0),
                data.get('unit_carbs', 0),
                data.get('unit_fiber', 0)
            )
        )
        
    conn.commit()
    conn.close()
    return jsonify({'status': 'succes'})

# 6. Update servings OR custom weight/volume in the daily log
@app.route('/api/update_portions/<int:id_meal>', methods=['PATCH'])
def update_portions(id_meal):
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    data = request.json
    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute(
        '''SELECT base_name, base_portion, unit_kcal, unit_protein, unit_fat, unit_carbs, unit_fiber, measure_mode 
           FROM meals WHERE id = ? AND user = ?''',
        (id_meal, session['username'])
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({'status': 'not_found'}), 404

    base_name, base_portion, u_kcal, u_p, u_f, u_c, u_fib, measure_mode = row

    if 'custom_weight' in data and measure_mode == 'weight':
        new_weight = float(data.get('custom_weight', 0))
        if new_weight <= 0:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Weight must be > 0'}), 400
        
        match = re.search(r'(\d+(?:\.\d+)?)\s*(g|ml)\b', base_portion, re.IGNORECASE)
        base_weight = float(match.group(1)) if match else 100.0
        multiplier = new_weight / base_weight

        new_kcal = round(u_kcal * multiplier)
        new_p = round(u_p * multiplier, 1)
        new_f = round(u_f * multiplier, 1)
        new_c = round(u_c * multiplier, 1)
        new_fib = round(u_fib * multiplier, 1)

        c.execute(
            '''UPDATE meals 
               SET aliment = ?, calories = ?, protein = ?, fat = ?, carbs = ?, fiber = ?, portions = ?, custom_weight = ?
               WHERE id = ? AND user = ?''',
            (base_name, new_kcal, new_p, new_f, new_c, new_fib, round(multiplier, 3), new_weight, id_meal, session['username'])
        )
    else:
        new_portions = float(data.get('portions', 1))
        if new_portions <= 0:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Servings must be > 0'}), 400

        new_kcal = round(u_kcal * new_portions)
        new_p = round(u_p * new_portions, 1)
        new_f = round(u_f * new_portions, 1)
        new_c = round(u_c * new_portions, 1)
        new_fib = round(u_fib * new_portions, 1)

        c.execute(
            '''UPDATE meals 
               SET aliment = ?, calories = ?, protein = ?, fat = ?, carbs = ?, fiber = ?, portions = ?
               WHERE id = ? AND user = ?''',
            (base_name, new_kcal, new_p, new_f, new_c, new_fib, new_portions, id_meal, session['username'])
        )

    conn.commit()
    conn.close()
    return jsonify({'status': 'updated'})

# 7. Get meals for logged-in user and selected date
@app.route('/api/meals')
def get_meals():
    if 'username' not in session:
        return jsonify([]), 401

    target_date = request.args.get('date') or str(date.today())
    current_user = session['username']
    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute(
        '''SELECT id, aliment, calories, protein, fat, carbs, fiber, portions, unit_kcal, 
                  base_name, measure_mode, custom_weight, weight_unit, meal_type 
           FROM meals WHERE user = ? AND data = ? ORDER BY id ASC''',
        (current_user, target_date)
    )
    lines = c.fetchall()
    conn.close()
    
    meals_list = []
    for l in lines:
        clean_name = l[9] if l[9] else re.sub(r'\s*\(.*?\)\s*$', '', l[1]).strip()
        m_type = (l[13] or 'breakfast').lower()
        if m_type not in VALID_MEAL_TYPES:
            m_type = 'breakfast'
        meals_list.append({
            'id': l[0],
            'name': clean_name,
            'calories': l[2],
            'protein': round(l[3], 1),
            'fat': round(l[4], 1),
            'carbs': round(l[5], 1),
            'fiber': round(l[6], 1),
            'portions': l[7],
            'can_edit': l[8] > 0,
            'measure_mode': l[10] or 'serving',
            'custom_weight': int(l[11]) if l[11] == int(l[11]) else l[11],
            'weight_unit': l[12] or 'g',
            'meal_type': m_type
        })
    return jsonify(meals_list)

@app.route('/api/logged_dates')
def get_logged_dates():
    if 'username' not in session:
        return jsonify([]), 401

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute(
        'SELECT DISTINCT data FROM meals WHERE user = ?',
        (session['username'],)
    )
    dates = [row[0] for row in c.fetchall()]
    conn.close()
    return jsonify(dates)

# 8. Delete meal
@app.route('/api/delete/<int:id_meal>', methods=['DELETE'])
def delete_meal(id_meal):
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute('DELETE FROM meals WHERE id = ? AND user = ?', (id_meal, session['username']))
    conn.commit()
    conn.close()
    return jsonify({'status': 'deleted'})

# 9. Body Weight Tracking Routes
@app.route('/api/weight', methods=['GET'])
def get_weights():
    if 'username' not in session:
        return jsonify([]), 401

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute(
        'SELECT id, weight_kg, data FROM weight_logs WHERE user = ? ORDER BY data DESC',
        (session['username'],)
    )
    rows = c.fetchall()
    conn.close()

    return jsonify([
        {'id': r[0], 'weight_kg': round(r[1], 1), 'date': r[2]}
        for r in rows
    ])

@app.route('/api/weight', methods=['POST'])
def save_weight():
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    data = request.json
    try:
        weight_kg = round(float(data.get('weight_kg', 0)), 1)
    except (ValueError, TypeError):
        weight_kg = 0

    target_date = data.get('date') or str(date.today())
    if weight_kg < 20 or weight_kg > 400:
        return jsonify({'status': 'error', 'message': 'Please enter a valid weight between 20 and 400 kg.'}), 400

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute(
        '''INSERT INTO weight_logs (user, weight_kg, data) 
           VALUES (?, ?, ?)
           ON CONFLICT(user, data) DO UPDATE SET weight_kg = excluded.weight_kg''',
        (session['username'], weight_kg, target_date)
    )
    c.execute('SELECT daily_goal FROM users WHERE username = ?', (session['username'],))
    u_row = c.fetchone()
    goal = u_row[0] if u_row else session.get('daily_goal', 2000)
    latest_w = get_user_latest_weight(c, session['username'])
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'succes',
        'macro_targets': compute_macro_targets(goal, latest_w)
    })

@app.route('/api/weight/<int:id_weight>', methods=['DELETE'])
def delete_weight(id_weight):
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute('DELETE FROM weight_logs WHERE id = ? AND user = ?', (id_weight, session['username']))
    c.execute('SELECT daily_goal FROM users WHERE username = ?', (session['username'],))
    u_row = c.fetchone()
    goal = u_row[0] if u_row else session.get('daily_goal', 2000)
    latest_w = get_user_latest_weight(c, session['username'])
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'deleted',
        'macro_targets': compute_macro_targets(goal, latest_w)
    })

# 10. User Profile Settings & AI Calorie/Macro Recommendation
def calculate_age(birth_date_str):
    if not birth_date_str:
        return None
    try:
        dob = datetime.strptime(birth_date_str, '%Y-%m-%d').date()
        today = date.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    except ValueError:
        return None

@app.route('/api/profile', methods=['GET'])
def get_profile():
    if 'username' not in session:
        return jsonify({}), 401

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute(
        'SELECT daily_goal, height_cm, birth_date, gender, activity_level FROM users WHERE username = ?',
        (session['username'],)
    )
    row = c.fetchone()
    latest_w = get_user_latest_weight(c, session['username'])
    conn.close()

    if not row:
        return jsonify({'status': 'not_found'}), 404

    daily_goal, height_cm, birth_date, gender, activity_level = row
    return jsonify({
        'daily_goal': daily_goal,
        'macro_targets': compute_macro_targets(daily_goal, latest_w),
        'height_cm': height_cm,
        'birth_date': birth_date,
        'age': calculate_age(birth_date),
        'gender': gender or 'unspecified',
        'activity_level': activity_level or 'moderate'
    })

@app.route('/api/profile', methods=['POST'])
def update_profile():
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    data = request.json
    try:
        daily_goal = int(data.get('daily_goal') or 2000)
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': 'Invalid calorie goal.'}), 400

    if daily_goal < 800 or daily_goal > 8000:
        return jsonify({'status': 'error', 'message': 'Daily calorie goal must be between 800 and 8000 kcal.'}), 400

    height_raw = data.get('height_cm')
    height_cm = None
    if height_raw not in (None, ''):
        try:
            height_cm = round(float(height_raw), 1)
            if height_cm < 80 or height_cm > 250:
                return jsonify({'status': 'error', 'message': 'Please enter a valid height between 80 and 250 cm.'}), 400
        except (ValueError, TypeError):
            return jsonify({'status': 'error', 'message': 'Invalid height value.'}), 400

    birth_date = (data.get('birth_date') or '').strip() or None
    if birth_date:
        age = calculate_age(birth_date)
        if age is None or age < 10 or age > 120:
            return jsonify({'status': 'error', 'message': 'Please enter a valid birth date.'}), 400

    gender = data.get('gender') or 'unspecified'
    activity_level = data.get('activity_level') or 'moderate'

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute(
        '''UPDATE users 
           SET daily_goal = ?, height_cm = ?, birth_date = ?, gender = ?, activity_level = ?
           WHERE username = ?''',
        (daily_goal, height_cm, birth_date, gender, activity_level, session['username'])
    )
    latest_w = get_user_latest_weight(c, session['username'])
    conn.commit()
    conn.close()

    session['daily_goal'] = daily_goal
    return jsonify({
        'status': 'succes',
        'daily_goal': daily_goal,
        'macro_targets': compute_macro_targets(daily_goal, latest_w),
        'age': calculate_age(birth_date)
    })

class CalorieAdviceText(BaseModel):
    advice: str

@app.route('/api/ai_calorie_recommendation', methods=['POST'])
def ai_calorie_recommendation():
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute(
        'SELECT height_cm, birth_date, gender, activity_level FROM users WHERE username = ?',
        (session['username'],)
    )
    user_row = c.fetchone()
    latest_weight = get_user_latest_weight(c, session['username'])
    conn.close()

    height_cm = user_row[0] if user_row else None
    birth_date = user_row[1] if user_row else None
    gender = (user_row[2] if user_row else None) or 'unspecified'
    activity_level = (user_row[3] if user_row else None) or 'moderate'
    age = calculate_age(birth_date)

    if not height_cm or not age or not latest_weight:
        missing = []
        if not latest_weight:
            missing.append("body weight")
        if not height_cm:
            missing.append("height")
        if not age:
            missing.append("date of birth")
        return jsonify({
            'status': 'error',
            'message': f"Please save your {', '.join(missing)} first so AI can calculate your personalized calorie needs."
        }), 400

    base_bmr = (10.0 * latest_weight) + (6.25 * height_cm) - (5.0 * age)
    if gender == 'male':
        bmr = base_bmr + 5.0
    elif gender == 'female':
        bmr = base_bmr - 161.0
    else:
        bmr = base_bmr - 78.0

    multipliers = {
        'sedentary': 1.2,
        'light': 1.375,
        'moderate': 1.55,
        'active': 1.725
    }
    mult = multipliers.get(activity_level, 1.375)
    raw_tdee = bmr * mult

    maintenance_kcal = int(round(raw_tdee / 50.0) * 50)
    weight_loss_kcal = max(1200, maintenance_kcal - 500)
    muscle_gain_kcal = maintenance_kcal + 300

    prompt = f"""
    You are an expert nutritionist. Write a concise 2-sentence explanation in English for this user's exact Mifflin-St Jeor calculation:
    - Stats: {age} yrs, {height_cm} cm, {latest_weight} kg, sex: {gender}, activity: {activity_level} (multiplier {mult})
    - Exact BMR: {round(bmr)} kcal/day
    - Weight Maintenance (TDEE): ~{maintenance_kcal} kcal/day
    - Moderate Fat Loss (-500 kcal deficit): ~{weight_loss_kcal} kcal/day
    - Lean Muscle Gain (+300 kcal surplus): ~{muscle_gain_kcal} kcal/day

    State their BMR ({round(bmr)} kcal) and Maintenance TDEE (~{maintenance_kcal} kcal) clearly, and mention that a ~500 kcal deficit (~{weight_loss_kcal} kcal) supports steady fat loss of ~0.5 kg/week. Use ONLY these exact numbers.
    """

    advice_text = (
        f"Your estimated BMR is {round(bmr)} kcal/day, giving a Weight Maintenance TDEE of ~{maintenance_kcal} kcal/day. "
        f"Aim for ~{weight_loss_kcal} kcal/day for steady fat loss (~0.5 kg/week) or ~{muscle_gain_kcal} kcal/day for lean muscle gain."
    )

    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CalorieAdviceText,
                temperature=0.1
            ),
        )
        rec = json.loads(response.text)
        if rec.get('advice'):
            advice_text = rec['advice']
    except Exception as e:
        print(f"--> AI Calorie Recommendation Error: {e}")

    return jsonify({
        'status': 'succes',
        'maintenance_kcal': maintenance_kcal,
        'weight_loss_kcal': weight_loss_kcal,
        'muscle_gain_kcal': muscle_gain_kcal,
        'macros_loss': compute_macro_targets(weight_loss_kcal, latest_weight),
        'macros_maint': compute_macro_targets(maintenance_kcal, latest_weight),
        'macros_gain': compute_macro_targets(muscle_gain_kcal, latest_weight),
        'advice': advice_text
    })

# 11. Mutual Friends & Progress Sharing Routes
@app.route('/api/friends', methods=['GET'])
def list_friends():
    if 'username' not in session:
        return jsonify({}), 401

    me = session['username']
    target_date = request.args.get('date') or str(date.today())

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()

    # Users I added
    c.execute('SELECT user_to FROM friendships WHERE user_from = ?', (me,))
    added_by_me = {r[0] for r in c.fetchall()}

    # Users who added me
    c.execute('SELECT user_from FROM friendships WHERE user_to = ?', (me,))
    added_me = {r[0] for r in c.fetchall()}

    mutual_usernames = sorted(added_by_me & added_me)
    outgoing_pending = sorted(added_by_me - added_me)
    incoming_requests = sorted(added_me - added_by_me)

    mutual_list = []
    for friend in mutual_usernames:
        c.execute('SELECT daily_goal FROM users WHERE username = ?', (friend,))
        u_row = c.fetchone()
        f_goal = u_row[0] if u_row else 2000

        c.execute(
            'SELECT COALESCE(SUM(calories), 0), COALESCE(SUM(protein), 0) FROM meals WHERE user = ? AND data = ?',
            (friend, target_date)
        )
        sum_row = c.fetchone()
        f_kcal = int(sum_row[0] or 0)
        f_prot = round(float(sum_row[1] or 0), 1)

        mutual_list.append({
            'username': friend,
            'daily_goal': f_goal,
            'calories': f_kcal,
            'protein': f_prot
        })

    conn.close()
    return jsonify({
        'mutual': mutual_list,
        'outgoing': outgoing_pending,
        'incoming': incoming_requests
    })

@app.route('/api/friends/add', methods=['POST'])
def add_friend():
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    me = session['username']
    data = request.json
    target = (data.get('username') or '').strip().lower()

    if not target:
        return jsonify({'status': 'error', 'message': 'Please enter a username.'}), 400
    if target == me:
        return jsonify({'status': 'error', 'message': 'You cannot add yourself as a friend.'}), 400

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()

    c.execute('SELECT username FROM users WHERE username = ?', (target,))
    if not c.fetchone():
        conn.close()
        return jsonify({'status': 'error', 'message': f'User "{target}" does not exist.'}), 404

    c.execute(
        'INSERT OR IGNORE INTO friendships (user_from, user_to, created_at) VALUES (?, ?, ?)',
        (me, target, datetime.now().isoformat())
    )

    c.execute('SELECT 1 FROM friendships WHERE user_from = ? AND user_to = ?', (target, me))
    is_mutual = c.fetchone() is not None

    conn.commit()
    conn.close()

    if is_mutual:
        return jsonify({
            'status': 'succes',
            'mutual': True,
            'message': f'You and {target} are now mutual friends! You can view their progress.'
        })
    return jsonify({
        'status': 'succes',
        'mutual': False,
        'message': f'Friend request sent to {target}. Progress unlocks once they add you back.'
    })

@app.route('/api/friends/remove', methods=['POST'])
def remove_friend():
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    me = session['username']
    data = request.json
    target = (data.get('username') or '').strip().lower()

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute('DELETE FROM friendships WHERE user_from = ? AND user_to = ?', (me, target))
    conn.commit()
    conn.close()
    return jsonify({'status': 'removed'})

@app.route('/api/friends/progress/<friend_username>', methods=['GET'])
def get_friend_progress(friend_username):
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    me = session['username']
    friend = friend_username.strip().lower()
    target_date = request.args.get('date') or str(date.today())

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()

    # Verify strict mutual friendship
    c.execute('SELECT 1 FROM friendships WHERE user_from = ? AND user_to = ?', (me, friend))
    i_added = c.fetchone() is not None
    c.execute('SELECT 1 FROM friendships WHERE user_from = ? AND user_to = ?', (friend, me))
    they_added = c.fetchone() is not None

    if not (i_added and they_added):
        conn.close()
        return jsonify({'status': 'forbidden', 'message': 'Mutual friendship is required to view progress.'}), 403

    c.execute('SELECT daily_goal FROM users WHERE username = ?', (friend,))
    u_row = c.fetchone()
    if not u_row:
        conn.close()
        return jsonify({'status': 'not_found'}), 404

    f_goal = u_row[0]
    f_weight = get_user_latest_weight(c, friend)
    f_macro_targets = compute_macro_targets(f_goal, f_weight)

    c.execute(
        '''SELECT id, aliment, calories, protein, fat, carbs, fiber, portions, base_name, meal_type
           FROM meals WHERE user = ? AND data = ? ORDER BY id ASC''',
        (friend, target_date)
    )
    rows = c.fetchall()
    conn.close()

    meals_list = []
    for r in rows:
        clean_name = r[8] if r[8] else re.sub(r'\s*\(.*?\)\s*$', '', r[1]).strip()
        m_type = (r[9] or 'breakfast').lower()
        if m_type not in VALID_MEAL_TYPES:
            m_type = 'breakfast'
        meals_list.append({
            'id': r[0],
            'name': clean_name,
            'calories': r[2],
            'protein': round(r[3], 1),
            'fat': round(r[4], 1),
            'carbs': round(r[5], 1),
            'fiber': round(r[6], 1),
            'portions': r[7],
            'meal_type': m_type
        })

    return jsonify({
        'status': 'succes',
        'username': friend,
        'date': target_date,
        'daily_goal': f_goal,
        'macro_targets': f_macro_targets,
        'meals': meals_list
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)