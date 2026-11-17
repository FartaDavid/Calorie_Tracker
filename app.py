import os
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
# Cheie secretă pentru criptarea cookie-urilor de sesiune
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "cheie-secreta-super-sigura-calorii-2026")
app.permanent_session_lifetime = timedelta(days=30)

load_dotenv(override=True)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

# 1. Inițializarea bazei de date SQLite
def init_db():
    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    
    # Tabel NOU pentru conturile utilizatorilor
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            daily_goal INTEGER NOT NULL DEFAULT 2000
        )
    ''')
    
    # Tabelul pentru mesele consumate (legat de username)
    c.execute('''
        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT NOT NULL,
            aliment TEXT NOT NULL,
            calories INTEGER NOT NULL,
            data TEXT NOT NULL
        )
    ''')
    
    # Catalogul comun de alimente pe porții
    c.execute('''
        CREATE TABLE IF NOT EXISTS alimente_locale (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nume TEXT UNIQUE NOT NULL,
            portie TEXT NOT NULL,
            kcal_portie INTEGER NOT NULL
        )
    ''')
    
    c.execute('SELECT COUNT(*) FROM alimente_locale')
    if c.fetchone()[0] == 0:
        alimente_initiale = [
            ('Banană medie', '1 bucată (~120g)', 105),
            ('Ou fiert / ochi', '1 bucată (~60g)', 78),
            ('Măr mediu', '1 bucată (~150g)', 80),
            ('Felie pâine integrală', '1 felie (~35g)', 85),
            ('Piept de pui la grătar', '1 porție (~150g)', 248),
            ('Cafea cu lapte', '1 cană (~200ml)', 60),
            ('Covrig cu susan', '1 bucată (~100g)', 280)
        ]
        c.executemany(
            'INSERT INTO alimente_locale (nume, portie, kcal_portie) VALUES (?, ?, ?)',
            alimente_initiale
        )
        
    conn.commit()
    conn.close()

init_db()

# 2. Pagina principală (trimite starea de logare către HTML)
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

# 3. Rute pentru Înregistrare, Login și Logout
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username', '').strip().lower()
    password = data.get('password', '').strip()
    daily_goal = int(data.get('daily_goal') or 2000)

    if len(username) < 3 or len(password) < 4:
        return jsonify({'status': 'error', 'message': 'Username-ul trebuie să aibă min. 3 caractere și parola min. 4.'}), 400

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    try:
        pw_hash = generate_password_hash(password)
        c.execute('INSERT INTO users (username, password_hash, daily_goal) VALUES (?, ?, ?)', (username, pw_hash, daily_goal))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Acest username există deja. Alege altul sau loghează-te.'}), 400
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
        return jsonify({'status': 'error', 'message': 'Username sau parolă incorectă.'}), 401

    session.permanent = True
    session['username'] = username
    session['daily_goal'] = row[1]
    return jsonify({'status': 'succes'})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('acasa'))

# 4. Căutare hibridă: Locală + AI Gemini
class SugestieAliment(BaseModel):
    nume: str
    portie: str
    kcal: int

class ListaSugestii(BaseModel):
    rezultate: list[SugestieAliment]

@app.route('/api/search')
def search_aliment():
    if 'username' not in session:
        return jsonify([]), 401

    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return jsonify([])

    rezultate_finale = []

    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute('SELECT nume, portie, kcal_portie FROM alimente_locale WHERE nume LIKE ? LIMIT 4', (f'%{query}%',))
    for r in c.fetchall():
        rezultate_finale.append({
            'nume': r[0],
            'portie': f"⭐ {r[1]}",
            'kcal': r[2]
        })
    conn.close()

    prompt = f"""
    Ești un expert nutriționist. Utilizatorul caută: "{query}".
    Oferă între 1 și 4 variante relevante în limba română (inclusiv produse românești dacă se potrivește, ex: Zuzu, Napolact, covrigi, sarmale).
    Reguli stricte:
    - 'nume': numele curat al alimentului (ex: "Banană medie", "Iaurt grecesc Zuzu 10%").
    - 'portie': unitatea naturală de măsură (ex: "1 bucată (~120g)", "1 pahar (150g)", "100g").
    - 'kcal': numărul exact de calorii PENTRU ACEA PORȚIE (nu la 100g, decât dacă porția este 100g).
    """

    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ListaSugestii,
                temperature=0.2
            ),
        )
        date_ai = json.loads(response.text)
        for item in date_ai.get('rezultate', []):
            if not any(r['nume'].lower() == item['nume'].lower() for r in rezultate_finale):
                rezultate_finale.append({
                    'nume': item['nume'].replace("'", "").replace('"', ''),
                    'portie': item['portie'].replace("'", "").replace('"', ''),
                    'kcal': item['kcal']
                })
    except Exception as e:
        print(f"--> Eroare AI: {e}")

    return jsonify(rezultate_finale)

# 5. Adăugare masă pentru utilizatorul logat în sesiune
@app.route('/api/add', methods=['POST'])
def add_meal():
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    data = request.json
    today = str(date.today())
    current_user = session['username']
    
    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    
    c.execute(
        'INSERT INTO meals (user, aliment, calories, data) VALUES (?, ?, ?, ?)',
        (current_user, data['aliment'], data['calorii'], today)
    )
    
    if data.get('nume_baza') and data.get('kcal_unitar'):
        c.execute(
            'INSERT OR IGNORE INTO alimente_locale (nume, portie, kcal_portie) VALUES (?, ?, ?)',
            (data['nume_baza'], data.get('portie_baza', '1 porție'), data['kcal_unitar'])
        )
        
    conn.commit()
    conn.close()
    return jsonify({'status': 'succes'})

# 6. Preluare mese exclusiv pentru utilizatorul logat
@app.route('/api/meals')
def get_meals():
    if 'username' not in session:
        return jsonify([]), 401

    today = str(date.today())
    current_user = session['username']
    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute(
        'SELECT id, aliment, calories FROM meals WHERE user = ? AND data = ? ORDER BY id DESC',
        (current_user, today)
    )
    lines = c.fetchall()
    conn.close()
    
    meals_list = [{'id': l[0], 'name': l[1], 'calories': l[2]} for l in lines]
    return jsonify(meals_list)

# 7. Ștergere masă (doar dacă aparține utilizatorului logat)
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