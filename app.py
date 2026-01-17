from flask import Flask, render_template, request, jsonify
from pymongo import MongoClient
import datetime
import threading
import os
from bson.objectid import ObjectId
import worker  # Import the worker module

app = Flask(__name__)

# MongoDB Connection
MONGO_URI = "mongodb+srv://harshmanjhi1801:webapp@cluster0.xxwc4.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['instabot_db']
tasks_collection = db['tasks']
logs_collection = db['logs']

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/tasks', methods=['GET', 'POST'])
def manage_tasks():
    if request.method == 'GET':
        tasks = list(tasks_collection.find().sort("created_at", -1))
        for task in tasks:
            task['_id'] = str(task['_id'])
        return jsonify(tasks)
    
    if request.method == 'POST':
        data = request.json
        task_type = data.get('type') 
        
        new_task = {
            "type": task_type,
            "status": "active",
            "reply_message": data.get('reply_message', ''),
            "created_at": datetime.datetime.now()
        }

        if task_type == 'comment_dm':
            new_task["post_url"] = data.get('post_url')
            new_task["keyword"] = data.get('keyword', 'Any')
        
        result = tasks_collection.insert_one(new_task)
        return jsonify({"message": "Task created", "id": str(result.inserted_id)})

@app.route('/api/tasks/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    tasks_collection.delete_one({"_id": ObjectId(task_id)})
    return jsonify({"message": "Task deleted"})

@app.route('/api/logs', methods=['GET'])
def get_logs():
    logs = list(logs_collection.find().sort("timestamp", -1).limit(50))
    for log in logs:
        log['_id'] = str(log['_id'])
    return jsonify(logs)

def start_worker_thread():
    # Only start the worker if it's not already running
    # The Flask reloader can cause this to run twice, checking WERKZEUG_RUN_MAIN helps
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
         # Run worker.main() in a separate thread
        print("Starting Bot Worker Thread...")
        bot_thread = threading.Thread(target=worker.main, daemon=True)
        bot_thread.start()

# Start worker when app is imported (for Gunicorn)
# We use a simple lock mechanism or just rely on single worker to avoid duplicates
if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    # In production (Gunicorn), this runs once per worker. 
    # Since we use 1 worker in Procfile (implied), this is fine.
    start_worker_thread()

if __name__ == '__main__':
    # For local testing
    app.run(debug=True, use_reloader=False, port=int(os.environ.get("PORT", 5000)))
