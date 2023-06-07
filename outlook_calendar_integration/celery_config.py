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
    response["google_expiry"] = int(time.mktime(expirationDateTime.timetuple()))*1000 
    
    response_request = requests.post(callback, data=response)
    return response



@celery_app.task
def incoming_ping(channel_id):
        
    user_request = requests.get(os.getenv("TOKEN_URL") + f"{channel_id}/")
    user = json.loads(user_request.text)
    try:
        user_uuid = user.get("uuid", "aa")
    except:
        return "Outlook channel not found"
    
    refresh_token = user.get("refresh", "")
    
    formatted_changed_events = fetch_changed_events(refresh_token)
    request_callback_ping = requests.post(os.getenv("EVENT_PING_CALLBACK_URL"), json={"channel": channel_id, "uuid": user_uuid, "tasks": formatted_changed_events})
    return f"{len(formatted_changed_events)} tasks found with at channel: {channel_id}"


######################
## HELPER FUNCTIONS ##
######################


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
    zoom_link_pattern = r'/https:\/\/[\w-]*\.?zoom.us\/(j|my)\/[\d\w?=-]+/g'
    meets_link_pattern = r'https:\/\/meet\.google\.com\/[a-z]+-[a-z]+-[a-z]+'
    teams_link_pattern = r'https:\/\/teams\.microsoft\.com\/l\/meetup-join\/[a-zA-Z0-9\/%]+'

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



def fetch_changed_events(refresh_token):
    return []
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_SECRET")

    # Create the credentials object
    creds = google.oauth2.credentials.Credentials.from_authorized_user_info(
        info={'client_id': client_id, 'client_secret': client_secret, 'refresh_token': refresh_token})

    # Initialize the Calendar API client
    service = build('calendar', 'v3', credentials=creds)

    # Your calendar ID
    calendar_id = 'primary'

    try:
        # Get the updated events using sync token

        tasks = []
        page_token = None
        now = datetime.now(timezone.utc)
        time_min = now
        time_max = now + timedelta(days=20)


        for i in range(1000):
            print(i)
            # Get the updated events using sync token
            now = datetime.now(timezone.utc)
            
            
            events_results = service.events().list(calendarId=calendar_id, syncToken=sync_token,
                                                   showDeleted=True, pageToken=page_token, singleEvents=True).execute()

            for event in events_results['items']:
                start_time = event['start'].get('dateTime', event['start'].get('date'))
                if is_within_time_range(start_time, time_min, time_max):
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

            # If there is a nextPageToken, update the sync_token variable and continue the loop
            if 'nextPageToken' in events_results:
                page_token = events_results['nextPageToken']
            else:
                break



        return tasks, events_results['nextSyncToken']
    except HttpError as error:
        print(f'An error occurred: {error}')
        return None, None
        
        
        
        


