import replicate
import requests
from urllib import parse
import json
import datetime
import os.path
from zoneinfo import ZoneInfo
import os
from datetime import datetime, timedelta
import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

import functions_framework
from googleapiclient.discovery import build


PROJECT_ID = "ig-event-bot"

class EventBot:
    def __init__(self) -> None:
        self.calendar_id = os.environ["CALENDAR_ID"]
        self.telegram_token = os.environ["TELEGRAM_TOKEN"]
        self.telegram_chat = os.environ["TELEGRAM_CHAT"]
        self.telegram_bot_secret = os.environ["TELEGRAM_BOT_SECRET"]
        self.replicate_api_token = os.environ["REPLICATE_API_TOKEN"]

    def run_replicate_model(self, uri: str):
        input = {
            "media": uri,
            "prompt": """I want to create a google calendar event from this flyer image (or screenshot with event details). 
            Please provide to results in valid JSON format.

            I want to extract the following:
            - Event title (JSON key this "title")
            - Date (JSON key this "date")
            - Start time (JSON key this "start_time"). Provide it as ISO timestamp
            If a year is not explicitly provided, use the current or upcoming year.
            For example, if its currently April 2025, and the event is for May, you would use May 2025). 
            For example, if its currently November 2025, and the event is for January, you would use January 2026). 
            DO NOT INCLUDE A TIME IF NO TIME IS PROVIDED ON THE FLYER. This indicates an all day event and time should be empty.
            - End time (JSON key this "end_time"). Provide it as ISO timestamp
            If a year is not explicitly provided, use the current or upcoming year.
            For example, if its currently April 2025, and the event is for May, you would use May 2025). 
            For example, if its currently November 2025, and the event is for January, you would use January 2026). 
            DO NOT INCLUDE A TIME IF NO TIME IS PROVIDED ON THE FLYER. This indicates an all day event and time should be empty.
            - Location name (JSON key this "location"). Name of location, not an address
            - Address (JSON key this "address"). This can be omitted if address is not provided
            - Description (JSON key this "description"). Any other text present on the flyer that describes the event
            - Image description (JSON key this "image_description"). Please describe what you can see from the image contents
            - Errors (JSON key this "error"). This is a JSON object that tracks if any of the above fields were unable to be extracted from the image, key should be for above fields,
            and value is a description of why you were not able to extract it. 
            Only put an entry here if the field was not able to be extracted, and only provide "error" at all if there is at least one error.

            Its possible I may accidentally give you an image that is not an event flyer. 
            Make sure that you are only processing this if you can find a valid date as text on the image. 
            Otherwise, mark all fields as error, and provide "image_description", but no other fields.
            """
        }
        logging.info("Calling replicate model")

        output = replicate.run(
            "lucataco/qwen3-vl-8b-instruct:39e893666996acf464cff75688ad49ac95ef54e9f1c688fbc677330acc478e11",
            input=input
        )
        logging.info(f"MODEL OUTPUT: {output}")
        return json.loads(output)

    @staticmethod
    def parse_dt(dt_str):
        # most events do not specify year, and the parser arbitrarily adds one
        # we assume events to be for this year, but it could be for next year (ex. currently Dec, posting events for Jan)
        # EDGE CASE - adding an event that already happened will be set to next year
        now = datetime.now().astimezone(ZoneInfo("America/New_York"))
        dt = datetime.fromisoformat(dt_str).replace(tzinfo=ZoneInfo("America/New_York"))
        dt = dt.replace(year=now.year)
        if dt < now:
            dt = dt.replace(year=dt.year + 1)
        return dt

    def create_calendar_event(self, uri, details):
        service = build("calendar", "v3")

        if not details["title"] or (not details["date"] and not details["start_time"]):
            self.send_telegram_message(
                f"""{uri}

                {details}

                Cannot create event, no title, date, or start time
                """,
            )
            logging.info("Cannot create event, no title, date, or start time")

        event = {
            "summary": details["title"],
            "location": details.get("location", "") + "\n" + details.get("address", ""),
            "description": uri + "\n\n" + details.get("description", ""),
            "start": {},
            "end": {},
        }
        
        if not details["start_time"]:
            # all day event
            dt = EventBot.parse_dt(details["date"]).strftime("%Y-%m-%d")
            event["start"] = {"date": dt}
            event["end"] = {"date": dt}
        else:
            start_date = EventBot.parse_dt(details["start_time"])
            start_dt = start_date.isoformat()
            event["start"] = {"dateTime": start_dt}
            if details["end_time"]:
                event["end"] = {"dateTime": EventBot.parse_dt(details["end_time"]).isoformat()}
            else:
                # no end time specified, end in 1hr 
                event["end"] = {"dateTime": (start_date + timedelta(hours=1)).isoformat()}

        logging.info(f"EVENT REQUEST: {event}")
        event = service.events().insert(calendarId=self.calendar_id, body=event, sendUpdates="all").execute()
        logging.info(f"EVENT: {event.get('htmlLink')}")

        self.send_telegram_message(
            msg=f"""
            Event created: {event.get('htmlLink')}

            {uri}

            {json.dumps(details, indent=4)}
            """,
        )
    
    def send_telegram_message(self, msg):
        requests.post(
            f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
            json={"chat_id": self.telegram_chat, "text": msg}
        )
    
    def get_telegram_photo_url(self, file_id):
        response = requests.get(
            f"https://api.telegram.org/bot{self.telegram_token}/getFile",
            params={"file_id": file_id},
        )
        file_path = response.json()["result"]["file_path"]
        return f"https://api.telegram.org/file/bot{self.telegram_token}/{file_path}"
            

@functions_framework.http
def app(request):
    try:
        request_json = request.get_json(silent=True)
        request_telegram_secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token')

        if request_telegram_secret != os.environ["TELEGRAM_BOT_SECRET"]:
            logging.error("Not authorized!")
            logging.info(request.headers)
            return "Not authorized!"
    except Exception as e:
        logging.error("Error getting request")
        logging.error(e)
        return "error getting request" # don't raise error because telegram will retry
        
    try:
        event_bot = EventBot()
        message = request_json["message"]

        event_link = None
        if "text" in message and "instagram.com" in message["text"]:
            uri = request_json["message"]["text"].strip()
            clean_uri = parse.urlunparse(parse.urlparse(uri)._replace(query=""))
            image_uri = f"{clean_uri}media/?size=l"
            event_link = uri
        elif "photo" in message:
            image_uri = event_bot.get_telegram_photo_url(message["photo"][-1]["file_id"])
            event_link = "[created from image]"
        else:
            raise ValueError(f"Unsupported message sent: {message}")

        model_results = event_bot.run_replicate_model(image_uri)
        event_bot.create_calendar_event(event_link, model_results)

        return "success!"
    except Exception as e:
        event_bot.send_telegram_message(
            msg=f"""Event creation ERROR
            {request_json}

            {e}
            """,
        )
        logging.error(e)
        return "error" # don't raise error because telegram will retry


# uncomment for testing
# if __name__ == "__main__":
#     event_bot = EventBot()
