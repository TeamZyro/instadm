import time
import datetime
import os
import random
from pymongo import MongoClient
from instagrapi import Client
from bson.objectid import ObjectId

# MongoDB Connection
MONGO_URI = "mongodb+srv://harshmanjhi1801:webapp@cluster0.xxwc4.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['instabot_db']
tasks_collection = db['tasks']
logs_collection = db['logs']

# Bot Configuration
SESSION_FILE = "session.json"
USERNAME = "INSERT_USERNAME_HERE"
PASSWORD = "INSERT_PASSWORD_HERE"

cl = Client()

def log(message, type="info"):
    print(f"[{type.upper()}] {message}")
    logs_collection.insert_one({
        "timestamp": datetime.datetime.now(),
        "message": message,
        "type": type
    })

def login():
    if os.path.exists(SESSION_FILE):
        try:
            cl.load_settings(SESSION_FILE)
            cl.get_timeline_feed()
            log("Logged in via session.")
            return True
        except Exception as e:
            log(f"Session login failed: {e}", "error")
    
    try:
        if USERNAME != "INSERT_USERNAME_HERE":
            cl.login(USERNAME, PASSWORD)
            cl.dump_settings(SESSION_FILE)
            log("Logged in via credentials.")
            return True
    except Exception as e:
        log(f"Credential login failed: {e}", "error")
        return False
    
    return False

# Global variable to track when the bot started
BOT_START_TIME = datetime.datetime.now().timestamp()

# --- Custom Classes for manual parsing ---
class SimpleUser:
    def __init__(self, pk, username):
        self.pk = pk
        self.username = username

class SimpleComment:
    def __init__(self, pk, user, text, created_at):
        self.pk = pk
        self.user = user
        self.text = text
        self.created_at = created_at

from pydantic import ValidationError

def fetch_comments_raw(media_pk):
    """
    Tries to fetch comments normally. If Pydantic validation fails (common with Reels),
    it falls back to parsing the raw JSON from the last response.
    """
    try:
        # Try standard fetch 
        # using chunk to be safe, though media_comments works too
        comments, _ = cl.media_comments_chunk(media_pk, max_amount=20)
        return comments
    except ValidationError:
        log("Pydantic Validation Error caught. Parsing raw last_json...", "info")
        try:
            # Fallback: Parse the raw JSON from the successful network request
            data = cl.last_json
            comments_data = data.get("comments", [])
            parsed_comments = []
            
            for c in comments_data:
                user_data = c.get("user", {})
                user = SimpleUser(user_data.get("pk"), user_data.get("username"))
                created_at = c.get("created_at", 0) 
                # Create a SimpleComment compatible object
                comment = SimpleComment(c.get("pk"), user, c.get("text", ""), created_at)
                parsed_comments.append(comment)
            return parsed_comments
        except Exception as e:
            log(f"Fallback parsing failed: {e}", "error")
            return []
    except Exception as e:
        # If it's not a validation error (e.g. Network Error), we can't do much
        # But wait, looking at the logs, the error string was "1 validation error for Media..."
        # This is a Pydantic error.
        
        # Check if it looks like a validation error if catching generic Exception
        if "validation error" in str(e):
             log("Validation Error caught (via generic). Parsing raw last_json...", "info")
             try:
                data = cl.last_json
                comments_data = data.get("comments", [])
                parsed_comments = []
                for c in comments_data:
                    user_data = c.get("user", {})
                    user = SimpleUser(user_data.get("pk"), user_data.get("username"))
                    created_at = c.get("created_at", 0) 
                    comment = SimpleComment(c.get("pk"), user, c.get("text", ""), created_at)
                    parsed_comments.append(comment)
                return parsed_comments
             except Exception as inner_e:
                log(f"Fallback parsing failed: {inner_e}", "error")
                return []
        
        log(f"Fetch failed: {e}", "error")
        raise e

def post_comment_raw(media_pk, text, replied_to_comment_id=None):
    """
    Manually posts a comment to bypass Pydantic validation on the response media object.
    """
    data = {
        "comment_text": text,
        "idempotence_token": cl.generate_uuid(),
        "inventory_source": "media_or_ad"
    }
    if replied_to_comment_id:
        data["replied_to_comment_id"] = replied_to_comment_id
        
    try:
        # Using with_action_data to ensure standard signed body
        cl.private_request(f"media/{media_pk}/comment/", data=cl.with_action_data(data))
        return True
    except Exception as e:
        log(f"Raw comment post failed: {e}", "error")
        raise e

def process_auto_dm_inbox(task):
    """
    Reads unread threads and replies.
    """
    try:
        threads = cl.direct_threads(amount=5)
        for thread in threads:
            messages = thread.messages
            if not messages: continue
            
            last_msg = messages[0]
            
            # Simple check: If last message is NOT me, reply.
            if last_msg.user_id != cl.user_id:
                cl.direct_send(task['reply_message'], thread_ids=[thread.id])
                log(f"Auto DM Reply sent to thread {thread.id}", "success")
                time.sleep(random.randint(2, 5))
                
    except Exception as e:
        log(f"Error in Inbox Auto DM: {e}", "error")

def process_comment_dm(task):
    """
    Checks comments on a post and DMs the commenter.
    """
    try:
        url = task['post_url']
        if "?" in url:
            url = url.split("?")[0]
            
        try:
            media_pk = cl.media_pk_from_url(url)
            log(f"Checking post {media_pk} for new comments...", "info")
        except Exception as e:
            log(f"Invalid URL or Media not found: {url} - {e}", "error")
            return

        try:
            comments = fetch_comments_raw(media_pk)
        except Exception as e:
            log(f"Failed to fetch comments: {e}", "error")
            return
        
        replied_comments = set(task.get('replied_comments', []))
        
        keyword = task.get('keyword', 'Any').lower()
        
        for comment in comments:
            comment_id_str = str(comment.pk)
            
            if comment_id_str in replied_comments:
                continue
                
            if comment.created_at < BOT_START_TIME:
                continue

            if str(comment.user.pk) == str(cl.user_id):
                continue

            should_reply = False
            comment_text = comment.text.lower()
            
            if keyword == 'any':
                should_reply = True
            elif keyword in comment_text:
                should_reply = True
            
            if should_reply:
                log(f"Found new comment from {comment.user.username}: {comment.text}", "info")
                try:
                    # 1. Send DM
                    cl.direct_send(task['reply_message'], user_ids=[comment.user.pk])
                    log(f"Sent DM to {comment.user.username}", "success")
                    
                    # 2. Reply to their comment
                    try:
                        post_comment_raw(media_pk, "Sent to DM done ✅", replied_to_comment_id=comment.pk)
                        log(f"Replied to comment by {comment.user.username}", "success")
                    except Exception as e:
                        log(f"Failed to reply to comment: {e}", "error")

                    # 3. Update DB immediately
                    tasks_collection.update_one(
                        {"_id": task["_id"]},
                        {"$addToSet": {"replied_comments": comment_id_str}}
                    )
                    
                    replied_comments.add(comment_id_str)
                    
                    time.sleep(random.randint(5, 10)) 
                except Exception as e:
                    log(f"Failed to process {comment.user.username}: {e}", "error")
            
    except Exception as e:
        log(f"Error processing post {task['post_url']}: {e}", "error")

def main():
    if not login():
        log("Bot failed to login. Exiting.", "error")
        return

    log("Bot Worker Started", "success")
    
    while True:
        try:
            tasks = list(tasks_collection.find({"status": "active"}))
            
            if not tasks:
                # print("No active tasks. Waiting...")
                pass
            
            for task in tasks:
                if task['type'] == 'auto_dm':
                    process_auto_dm_inbox(task)
                elif task['type'] == 'comment_dm':
                    process_comment_dm(task)
                
                time.sleep(2) 
            
            time.sleep(10) # Global loop delay
            
        except Exception as e:
            log(f"Worker Loop Error: {e}", "error")
            time.sleep(10)

if __name__ == "__main__":
    main()
