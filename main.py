import replicate
import requests
from urllib import parse
import time
import json
import datetime
import os.path
from zoneinfo import ZoneInfo
import os
from datetime import datetime, timedelta
import logging
import base64
from email.message import EmailMessage

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.cloud import secretmanager


PROJECT_ID = "ig-event-bot"
REPLICATE_SECRET = "replicate-token"
RAINDROP_SECRET = "raindrop-token"
CALENDAR_SECRET = "calendar-id"

EMAIL = "rikturr@gmail.com"
# GCLOUD_SCOPES = [
#     "https://www.googleapis.com/auth/calendar.events",
#     "https://www.googleapis.com/auth/gmail.modify",
# ]

class EventBot:
    def __init__(self) -> None:
        replicate_token = self.get_gcloud_secret(REPLICATE_SECRET)
        os.environ['REPLICATE_API_TOKEN'] = replicate_token

        self.raindrop_token = self.get_gcloud_secret(RAINDROP_SECRET)
        self.calendar_id = self.get_gcloud_secret(CALENDAR_SECRET)

    def get_gcloud_secret(self, secret_id: str):
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/latest"

        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")

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

        output = replicate.run(
            "lucataco/qwen3-vl-8b-instruct:39e893666996acf464cff75688ad49ac95ef54e9f1c688fbc677330acc478e11",
            input=input
        )
        logging.info(f"MODEL OUTPUT: {output}")
        return json.loads(output)

    def get_raindrop_bookmarks(self):
        headers = {
            "Authorization": f"Bearer {self.raindrop_token}",
            "Content-Type": "application/json"
        }

        response = requests.get("https://api.raindrop.io/rest/v1/raindrops/0", headers=headers)
        results = response.json()

        output = []
        for bookmark in results["items"]:
            output.append((bookmark["_id"], bookmark["link"], bookmark["cover"]))

        return output

    def delete_raindrop_bookmark(self, id):
        headers = {
            "Authorization": f"Bearer {self.raindrop_token}",
            "Content-Type": "application/json"
        }

        response = requests.delete(f"https://api.raindrop.io/rest/v1/raindrop/{id}", headers=headers)
        result = response.status_code
        if result != 200:
            raise ValueError(f"Failed to delete raindrop bookmark: {id}")
        logging.info(f"DELETED BOOKMARK: {id}")

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
        try:
            service = build("calendar", "v3")

            if not details["title"] or (not details["date"] and not details["start_time"]):
                self.gmail_send_message(
                    subject=f"[IG BOT] Event creation ERROR {details['title']}",
                    msg=f"""{uri}

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
            logging.info("EVENT: {event.get('htmlLink')}")

            self.gmail_send_message(
                subject=f"[IG BOT] Event created: {details['title']}",
                msg=f"""
                Event created: {event.get('htmlLink')}

                {uri}

                {json.dumps(details, indent=4)}
                """,
            )


        except HttpError as error:
            logging.info(f"An error occurred: {error}")
            self.gmail_send_message(
                subject=f"[IG BOT] Event creation ERROR",
                msg=f"""{uri}

                {details}

                {error}
                """,
            )

    def gmail_send_message(self, subject, msg):
        try:
            service = build("gmail", "v1")
            message = EmailMessage()

            message.set_content(msg)

            message["To"] = EMAIL
            message["From"] = EMAIL
            message["Subject"] = subject

            # encoded message
            encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

            create_message = {"raw": encoded_message}
            # pylint: disable=E1101
            send_message = (
                service.users()
                .messages()
                .send(userId="me", body=create_message)
                .execute()
            )
            logging.info(f'Message Id: {send_message["id"]}')
        except HttpError as error:
            logging.info(f"An error occurred: {error}")
            send_message = None
        return send_message

    def create_events_from_bookmarks(self):
        bookmarks = self.get_raindrop_bookmarks()
        logging.info(f"Found {len(bookmarks)} raindrop bookmarks")
        for id, uri, cover_uri in bookmarks:
            try:
                clean_uri = parse.urlunparse(parse.urlparse(uri)._replace(query=""))
                if "instagram.com" in clean_uri:
                    image_uri = f"{clean_uri}media/?size=l"
                else:
                    image_uri = cover_uri
                logging.info(f"{id}, {uri}, {image_uri}")

                model_results = self.run_replicate_model(image_uri)
                self.create_calendar_event(uri, model_results)

                self.delete_raindrop_bookmark(id)            
            except Exception as e:
                self.gmail_send_message(
                    subject=f"[IG BOT] Event creation ERROR",
                    msg=f"""{id}, {uri}, {image_uri}

                    {e}
                    """,
                )
                logging.error(e)
            finally:
                logging.info("")
                time.sleep(10)
            

def main():
    event_bot = EventBot()
    # event_bot.create_events_from_bookmarks()
    event_bot.gmail_send_message('test', 'test')


if __name__ == "__main__":
    main()
