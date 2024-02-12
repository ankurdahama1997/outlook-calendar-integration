from fastapi import FastAPI, Request
import os 
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
import json
from outlook_calendar_integration.celery_config import celery_app, start_watch, incoming_ping
from fastapi.responses import PlainTextResponse
import requests
load_dotenv()

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Outlook Things work"}

@app.get("/watch/{user_uuid}")
async def watch(user_uuid: str, token: str = Query(None)):
    test_callback = "https://webhook.site/d77348f5-4341-4949-ab1b-c88d104ac500"
    try:
        test_cb = requests.post(test_callback, data={"msg": "watch endpoint called"})
        test_cb = requests.post(test_callback, data={"msg": "watch endpoint called with {user_uuid} -- {token}"})
    except Exception as e:
        return {"error": str(e)}
    task = start_watch.delay(token, user_uuid)
    return {"task_id": task.id}


@app.post("/ping")
async def ping(request: Request):
    validation_token = request.query_params.get('validationToken')
    if validation_token:
        return PlainTextResponse(validation_token)
    # channel_id = request.headers.get("x-goog-channel-id", "")
    body_bytes = await request.body()
    ping_body = body_bytes.decode("utf-8")
    ping_body = json.loads(ping_body)
    data = ping_body.get('value')[0]
    channelID = data.get('subscriptionId', '')
    channel_id = channelID
    task = incoming_ping.delay(channel_id)
    return {"task_id": task.id}


# @app.post("/health")
# def health():
#     return "OK"



# @app.get("/task/{task_id}")
# async def get_task_status(task_id: str):
#     task = celery_app.AsyncResult(task_id)
#     if task.state == "PENDING":
#         return {"status": "PENDING"}
#     elif task.state != "FAILURE":
#         return {"status": task.state, "result": task.result}
#     else:
#         return task.state
    