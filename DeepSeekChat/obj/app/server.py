from flask import Flask, request, jsonify, render_template, g
import os
import uuid
import sqlite3
import json
import requests
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['DATABASE'] = 'chats.db'
app.config['ALLOWED_EXTENSIONS'] = {'txt', 'pdf', 'docx'}

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Database setup
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(app.config['DATABASE'])
        db.row_factory = sqlite3.Row
    return db

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                name TEXT,
                messages TEXT
            )
        ''')
        db.commit()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# Initialize database
init_db()

# Helper functions
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def save_chat(chat_id, name, messages):
    db = get_db()
    db.execute('''
        INSERT OR REPLACE INTO chats (id, name, messages)
        VALUES (?, ?, ?)
    ''', (chat_id, name, json.dumps(messages)))
    db.commit()

def load_chat(chat_id):
    db = get_db()
    row = db.execute('SELECT * FROM chats WHERE id = ?', (chat_id,)).fetchone()
    return dict(row) if row else None

def load_all_chats():
    db = get_db()
    rows = db.execute('SELECT id, name FROM chats').fetchall()
    return {row['id']: row['name'] for row in rows}

# Routes
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/chats', methods=['GET'])
def get_chats():
    db = get_db()
    rows = db.execute('SELECT id, name FROM chats').fetchall()
    return jsonify([dict(row) for row in rows])  # Return array of {id, name} objects

@app.route('/api/chats/new', methods=['POST'])
def new_chat():
    try:
        chat_id = str(uuid.uuid4())
        chat_name = request.json.get('name', f'Chat-{len(load_all_chats())+1}')
        save_chat(chat_id, chat_name, [])
        return jsonify({"chat_id": chat_id, "name": chat_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/chat/<chat_id>', methods=['GET'])
def get_chat(chat_id):
    try:
        chat = load_chat(chat_id)
        if not chat:
            return jsonify({"error": "Chat not found"}), 404
            
        # Convert messages JSON string to list
        chat['messages'] = json.loads(chat['messages'])
        return jsonify(chat)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/chat', methods=['POST'])
def handle_chat():
    try:
        data = request.json
        chat_id = data.get('chat_id')
        model = data.get('model', 'deepseek-chat')  # Default to 'deepseek-chat'
        messages = data.get('messages', [])

        # Validate model
        valid_models = ['deepseek-chat', 'deepseek-reasoner']
        if model not in valid_models:
            model = 'deepseek-chat'  # Fallback to default model

        if not chat_id:
            return jsonify({"error": "Missing chat ID"}), 400

        chat_data = load_chat(chat_id)
        if not chat_data:
            return jsonify({"error": "Chat not found"}), 404

        # Prepare API request
        headers = {
            "Authorization": f"Bearer {os.getenv('DEEPSEEK_API_KEY')}",
            "Content-Type": "application/json"
        }

        # Combine existing messages with new ones
        existing_messages = json.loads(chat_data['messages'])
        all_messages = existing_messages + messages

        # Log the payload
        print("Sending payload to DeepSeek API:", {
            "model": model,
            "messages": all_messages,
            "temperature": 0.0
        })

        # Make API call
        try:
            response = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": all_messages,
                    "temperature": 0.0
                }
            )
            response.raise_for_status()  # Raise an exception for HTTP errors
        except requests.exceptions.RequestException as e:
            print("API Request Failed:", e)
            print("Response Content:", e.response.content if e.response else "No response")
            return jsonify({"error": f"API Error: {str(e)}"}), 500

        # Process response
        ai_response = response.json()
        ai_message = ai_response['choices'][0]['message']

        # Update chat history
        updated_messages = all_messages + [ai_message]
        save_chat(chat_id, chat_data['name'], updated_messages)

        return jsonify(ai_response)

    except Exception as e:
        print("Unexpected Error:", e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "Empty filename"}), 400

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            # Read file content
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            os.remove(filepath)

            return jsonify({
                "choices": [{
                    "message": {
                        "content": f"```{filename.split('.')[-1]}\n{content}\n```",
                        "role": "assistant"
                    }
                }]
            })

        return jsonify({"error": "Unsupported file type"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)