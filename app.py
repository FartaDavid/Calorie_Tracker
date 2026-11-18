import os
import re
import json
import sqlite3
from datetime import date, timedelta
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

# 1. Database initialization + automatic migration
def init_db():
    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            daily_goal INTEGER NOT NULL DEFAULT 2000
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

    # Safe migrations for existing databases
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
        'weight_unit': "TEXT NOT NULL DEFAULT 'g'"
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

# 2. Home route
@app.route('/')
def acasa():
    if 'username' in session:
        return render_template(
            'index.html',
            logged_in=True,
            username=session['username'],
            daily_goal=session.get('daily_goal', 2000)
        )
    return render_template('index.html', logged_in=False, username='', daily_goal=2000)

# 3. Auth routes
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username', '').strip().lower()
    password = data.get('password', '').strip()
    daily_goal = int(data.get('daily_goal') or 2000)

    if len(username) < 3 or len(password) < 4:
        return jsonify({'status': 'error', 'message': 'Username must be at least 3 characters and password at least 4.'}), 400

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    try:
        pw_hash = generate_password_hash(password)
        c.execute('INSERT INTO users (username, password_hash, daily_goal) VALUES (?, ?, ?)', (username, pw_hash, daily_goal))
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

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute('SELECT password_hash, daily_goal FROM users WHERE username = ?', (username,))
    row = c.fetchone()
    conn.close()

    if not row or not check_password_hash(row[0], password):
        return jsonify({'status': 'error', 'message': 'Invalid username or password.'}), 401

    session.permanent = True
    session['username'] = username
    session['daily_goal'] = row[1]
    return jsonify({'status': 'succes'})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('acasa'))

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

# 5. Add meal for the selected date (defaults to today)
@app.route('/api/add', methods=['POST'])
def add_meal():
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    data = request.json
    target_date = data.get('date') or str(date.today())
    current_user = session['username']
    
    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    
    c.execute(
        '''INSERT INTO meals (
               user, aliment, calories, protein, fat, carbs, fiber,
               portions, unit_kcal, unit_protein, unit_fat, unit_carbs, unit_fiber,
               base_name, base_portion, measure_mode, custom_weight, weight_unit, data
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
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
                  base_name, measure_mode, custom_weight, weight_unit 
           FROM meals WHERE user = ? AND data = ? ORDER BY id DESC''',
        (current_user, target_date)
    )
    lines = c.fetchall()
    conn.close()
    
    meals_list = []
    for l in lines:
        clean_name = l[9] if l[9] else re.sub(r'\s*\(.*?\)\s*$', '', l[1]).strip()
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
            'weight_unit': l[12] or 'g'
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)