import os
import json
import sqlite3
from datetime import date
from flask import Flask, render_template, request, jsonify
from google import genai
from google.genai import types
from pydantic import BaseModel

app = Flask(__name__)

# Recomandat: setează cheia în terminal cu `export GEMINI_API_KEY="cheia_ta"`
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)
print("--- Modele disponibile pe cheia ta ---")
for m in client.models.list():
    if "flash" in m.name.lower() or "lite" in m.name.lower():
        print(m.name)
print("--------------------------------------")

# 1. Inițializarea bazei de date SQLite
def init_db():
    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT NOT NULL,
            aliment TEXT NOT NULL,
            calories INTEGER NOT NULL,
            data TEXT NOT NULL
        )
    ''')
    
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

# 2. Pagina principală
@app.route('/')
def acasa():
    return render_template('index.html')

# 3. Căutare hibridă: Locală (instant) + AI Gemini (porții naturale)
class SugestieAliment(BaseModel):
    nume: str
    portie: str
    kcal: int

class ListaSugestii(BaseModel):
    rezultate: list[SugestieAliment]

@app.route('/api/search')
def search_aliment():
    query = request.args.get('q', '').strip()
    print(f"--> [SEARCH] Am primit de la browser: '{query}'")
    if len(query) < 2:
        return jsonify([])

    rezultate_finale = []

    # A. Căutăm mai întâi în baza de date locală
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

    # B. Completăm cu AI Gemini pentru porții naturale și produse din România
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
            # Evităm duplicatele dacă există deja în local
            if not any(r['nume'].lower() == item['nume'].lower() for r in rezultate_finale):
                rezultate_finale.append({
                    'nume': item['nume'].replace("'", "").replace('"', ''),
                    'portie': item['portie'].replace("'", "").replace('"', ''),
                    'kcal': item['kcal']
                })
    except Exception as e:
        print(f"--> Eroare AI: {e}")

    return jsonify(rezultate_finale)

# 4. Adăugare masă + salvare automată în catalogul local
@app.route('/api/add', methods=['POST'])
def add_meal():
    data = request.json
    today = str(date.today())
    
    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    
    # Salvăm în jurnalul zilnic
    c.execute(
        'INSERT INTO meals (user, aliment, calories, data) VALUES (?, ?, ?, ?)',
        (data['utilizator'], data['aliment'], data['calorii'], today)
    )
    
    # Învățăm automat alimentul în catalogul local pentru viitor
    if data.get('nume_baza') and data.get('kcal_unitar'):
        c.execute(
            'INSERT OR IGNORE INTO alimente_locale (nume, portie, kcal_portie) VALUES (?, ?, ?)',
            (data['nume_baza'], data.get('portie_baza', '1 porție'), data['kcal_unitar'])
        )
        
    conn.commit()
    conn.close()
    return jsonify({'status': 'succes'})

# 5. Preluare mese pentru ziua curentă
@app.route('/api/meals/<user>')
def get_meals(user):
    today = str(date.today())
    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute(
        'SELECT id, aliment, calories FROM meals WHERE user = ? AND data = ? ORDER BY id DESC',
        (user, today)
    )
    lines = c.fetchall()
    conn.close()
    
    meals_list = [{'id': l[0], 'name': l[1], 'calories': l[2]} for l in lines]
    return jsonify(meals_list)

# 6. Ștergere masă
@app.route('/api/delete/<int:id_meal>', methods=['DELETE'])
def delete_meal(id_meal):
    conn = sqlite3.connect('calories.db')
    c = conn.cursor()
    c.execute('DELETE FROM meals WHERE id = ?', (id_meal,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'deleted'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)