from celery import Celery
import time
import uuid
import json
import os 
from dotenv import load_dotenv
import requests
import re

from datetime import datetime, timezone, timedelta
load_dotenv()


celery_app = Celery(
    "outlook-calendar-integration",
    broker=f"redis://{os.getenv('REDIS_URL')}:6379/1",
    backend=f"redis://{os.getenv('REDIS_URL')}:6379/1",
)

celery_app.conf.task_routes = {
    "outlook-calendar-integration.tasks.*": {"queue": "outlook-calendar-integration_queue"},
}

@celery_app.task
def start_watch(refresh_token, user_uuid):
    callback = os.getenv("WATCH_CALLBACK_URL")
    new_uuid = str(uuid.uuid4())
    expirationDateTime = (datetime.now()+timedelta(minutes=3600))
    watch_body = json.dumps({
        "changeType": "created,updated,deleted",
        "notificationUrl": os.getenv("EVENT_PING_URL"),
        "resource": "me/events",
        "expirationDateTime": expirationDateTime.strftime('%Y-%m-%dT%H:%M:%S.%f0Z'),
        "clientState": "SecretClientState"
    })
    
    try:
        token, token_type = getToken(refresh_token)
    except Exception as e:
        response_request = requests.post(callback, data={"msg": "Failed"})
        return str(e)
    
    watch_url = "https://graph.microsoft.com/v1.0/subscriptions"
    request_watch = requests.post(watch_url, data=watch_body, headers={'Content-Type': 'application/json', 'Authorization': token_type + " " + token})
    response_watch = json.loads(request_watch.text)
    response = {}
    response["uuid"] = user_uuid.rstrip()
    response["google_channel"] = response_watch.get('id')
    if not response_watch.get('id'):
        print("Id not in response", flush=True)
        print(request_watch.text)
        raise ValueError("Id not in response")
    response["google_expiry"] = int(time.mktime(expirationDateTime.timetuple()))*1000 
    response["service"] = "outlook"
    
    response_request = requests.post(callback, data=response)
    return response



@celery_app.task
def incoming_ping(channel_id):
        
    user_request = requests.get(os.getenv("TOKEN_URL") + f"{channel_id}/")
    user = json.loads(user_request.text)
    try:
        user_uuid = user.get("uuid", "aa")
        email = user.get("email", "")
    except:
        return "Outlook channel not found"
    
    refresh_token = user.get("refresh", "")
    
    formatted_changed_events = fetch_changed_events(refresh_token, email)
    request_callback_ping = requests.post(os.getenv("EVENT_PING_CALLBACK_URL"), json={"channel": channel_id, "uuid": user_uuid, "tasks": formatted_changed_events})
    return f"{len(formatted_changed_events)} tasks found with at channel: {channel_id}"


######################
## HELPER FUNCTIONS ##
######################
class Profile:
    def __init__(self, calendar_token, user_email):
        self.calendar_token = calendar_token
        self.user = User(user_email)

class User:
    def __init__(self, email):
        self.email = email

def getToken(refresh_token):
    token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    token_data = {
        "grant_type": "refresh_token",
        "client_id": os.getenv("OUTLOOK_CLIENT_ID"),
        "client_secret": os.getenv("OUTLOOK_SECRET"),
        "refresh_token": refresh_token,
    }
    response = requests.post(token_url, data=token_data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
    response.raise_for_status()
    access_token = response.json()["access_token"]
    try:
        token_type = response.json()["token_type"]
    except:
        token_type = "Bearer"
    return access_token, token_type



# Function to find the link in the event description
def find_link(description):
    zoom_link_pattern = r'https:\/\/[\w-]*\.?zoom.us\/(j|my)\/[\d\w?=-]*\?pwd=[\d\w?=-]+'
    meets_link_pattern = r'https:\/\/meet\.google\.com\/[a-z]+-[a-z]+-[a-z]+'
    teams_link_pattern = r'https:\/\/(?:teams\.microsoft\.com\/l\/meetup-join|teams\.live\.com\/meet)\/[a-zA-Z0-9\/%]+(?:[a-zA-Z0-9-._~:/?#[\]@!$&\'()*+,;=%])*'

    zoom_match = re.search(zoom_link_pattern, description)
    if zoom_match:
        return zoom_match.group(0)

    teams_match = re.search(teams_link_pattern, description)
    if teams_match:
        return teams_match.group(0)

    meets_match = re.search(meets_link_pattern, description)
    if meets_match:
        return meets_match.group(0)
    
    return ""


def is_within_time_range(event_start, time_min, time_max):
    start_time_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
    return time_min <= start_time_dt <= time_max

def simplify_ms_event(event, email):
    attendees = []

    if event.get("organizer", {}).get("emailAddress", {}).get("address", "") == email:
        organizer = {"email": event.get("organizer", {}).get(
            "emailAddress", {}).get("address", ""), "self": True}
    else:
        organizer = {"email": event.get("organizer", {}).get(
            "emailAddress", {}).get("address", "")}
    for attendee in event.get("attendees", []):

        new_person = {'email': attendee.get("emailAddress", {}).get(
            "address", ""), 'name': attendee.get("emailAddress", {}).get("name", "")}
        if new_person["email"] == organizer["email"]:
            new_person["organizer"] = True
        if new_person["email"] == email:
            new_person["self"] = True
        attendees.append(new_person)

    parsed = {'summary': event.get("subject", ""), 'id': event.get("id", ""), 'start': {'dateTime': event.get(
        "start", {}).get("dateTime", "").split(".")[0]}, 'attendees': attendees, "organizer": organizer}
    desc_body = event.get("body", {}).get("content", "")
    try:
        try_main = event.get("onlineMeeting", {}).get("joinUrl", "")
    except:
        try_main = ""
    parsed["link"] = try_main
    if "@removed" in event:
        parsed['status'] = 'cancelled'
    if try_main == "":
        link = find_link(json.dumps(event))
        parsed["link"] = link

    return parsed

def getMSEvent(id, refresh_token, email):
    token, token_type = getToken(refresh_token)
    event = requests.get(f"https://graph.microsoft.com/v1.0/me/events/{id}", headers={'Content-Type': 'application/x-www-form-url-encoded', 'Authorization': token_type + " " + token})
    event = simplify_ms_event(json.loads(event.text), email)
    return event

def getEvents(profile):
    token, token_type = getToken(profile.calendar_token)
    headers={'Content-Type': 'application/x-www-form-url-encoded', 'Authorization': token_type + " " + token}
    all_events = []
    max_loops = 100
    while True:
        max_loops -= 1
        if max_loops < 0:
            break

        start_datetime = datetime.now().isoformat()
        end_datetime = (datetime.now() + timedelta(days=4)).isoformat()
        url = f"https://graph.microsoft.com/v1.0/me/calendarView/delta?startDateTime={start_datetime}&endDateTime={end_datetime}"
        
        res = requests.get(url,headers=headers)
        result = json.loads(res.text)
        
        for event in result.get('value', []):
            if event.get('type', '') == "seriesMaster":
                continue
            if event.get("type", "") == "occurrence":
                e = getMSEvent(event.get("id",''), profile.calendar_token, profile.user.email)
            else:
                e = simplify_ms_event(event, profile.user.email)
            all_events.append(e)

        next_page = result.get('@odata.nextLink', '')
        if not next_page:
            break
    
    return all_events

def fetch_changed_events(refresh_token, email):
    #return []
    profile_object = Profile(refresh_token, email)
    
    events = getEvents(profile_object)

    tasks = []
    for event in events:

        if 'status' in event and event['status'] == 'cancelled':
            tasks.append({'DELETE': [event['id']]})
        else:
            link = find_link(json.dumps(event))
            tasks.append({'UPDATE': [event['id'],
                                    link,
                                    event['start'].get('dateTime', event['start'].get('date')),
                                    event.get('summary', ''),
                                    event.get('organizer', {}),
                                    [attendee['email'] for attendee in event.get('attendees', []) if "resource.calendar.google.com" not in attendee['email']]]})
    return tasks
