import replicate
import requests
from urllib import parse
import time
import json
import datetime
import os.path
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


REPLICATE_TOKEN = open("creds/replicate_token.txt").read().strip()
RAINDROP_TOKEN = open("creds/raindrop_token.txt").read().strip()

CALENDAR_ID = open("creds/calendar_id.txt").read().strip()
GCLOUD_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def run_replicate_model(uri: str):
    input = {
        "media": uri,
        "prompt": """I want to create a google calendar event from this flyer image. 
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
    print("MODEL OUTPUT: ", output)
    return json.loads(output)


def get_raindrop_bookmarks():
    headers = {
        "Authorization": f"Bearer {RAINDROP_TOKEN}",
        "Content-Type": "application/json"
    }

    response = requests.get("https://api.raindrop.io/rest/v1/raindrops/0", headers=headers)
    results = response.json()

    output = []
    for bookmark in results["items"]:
        output.append((bookmark["_id"], bookmark["link"]))

    return output


def delete_raindrop_bookmark(id):
    headers = {
        "Authorization": f"Bearer {RAINDROP_TOKEN}",
        "Content-Type": "application/json"
    }

    response = requests.delete(f"https://api.raindrop.io/rest/v1/raindrop/{id}", headers=headers)
    result = response.status_code
    if result != 200:
        raise ValueError(f"Failed to delete raindrop bookmark: {id}")
    print("DELETED BOOKMARK", id)
    

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



def create_calendar_event(uri, details):
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", GCLOUD_SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", GCLOUD_SCOPES
            )
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    try:
        service = build("calendar", "v3", credentials=creds)

        event = {
            "summary": details["title"],
            "location": details["location"] + "\n" + details["address"],
            "description": uri + "\n\n" + details["description"],
            "start": {},
            "end": {},
        }

        if not details["date"] and not details["start_time"]:
            raise ValueError("Cannot create event, no date or start time")
        
        
        if not details["start_time"]:
            # all day event
            dt = parse_dt(details["date"]).strftime("%Y-%m-%d")
            event["start"] = {"date": dt}
            event["end"] = {"date": dt}
        else:
            start_dt = parse_dt(details["start_time"]).isoformat()
            event["start"] = {"dateTime": start_dt}
            if details["end_time"]:
                event["end"] = {"dateTime": parse_dt(details["end_time"]).isoformat()}
            else:
                # no end time specified, end in 1hr 
                event["end"] = {"dateTime": (start_dt + timedelta(hours=1)).isoformat()}

        print("EVENT REQUEST", event)
        event = service.events().insert(calendarId=CALENDAR_ID, body=event, sendUpdates="all").execute()
        print("EVENT", event.get('htmlLink'))


    except HttpError as error:
        print(f"An error occurred: {error}")


if __name__ == "__main__":
    print()
    print()
    # shamel garden event
    # print(run_replicate_model("https://scontent-lga3-2.cdninstagram.com/v/t51.82787-15/671174124_18175551667399104_5145419071611959600_n.jpg?stp=dst-jpg_e35_p1080x1080_sh0.08_tt6&_nc_ht=scontent-lga3-2.cdninstagram.com&_nc_cat=100&_nc_oc=Q6cZ2gGPzX2BLZLxKGVLxlJCON2H9tLkdxoEasxLi9DSp5luuVEpbTN8ktiWPZE_XvuVFE0&_nc_ohc=VFg2h0gcLAUQ7kNvwGptTLF&_nc_gid=Knoh-Fpai4bbwQnbNfbJXQ&edm=AGenrX8BAAAA&ccb=7-5&oh=00_Af0VGB7Bsums490mdzZCp7v-zDBuY48hOcInquPKNUXJWQ&oe=69E6FCED&_nc_sid=ed990e"))

    # non event - news post
    # run_replicate_model("https://scontent-lga3-2.cdninstagram.com/v/t51.82787-15/670402932_18575234383041096_7400461482863351718_n.jpg?stp=dst-jpg_e15_fr_p1080x1080_tt6&_nc_ht=scontent-lga3-2.cdninstagram.com&_nc_cat=1&_nc_oc=Q6cZ2gEzERtl06Rl0ZVbsle8ZnmWzKL7V9LQ3z0x7ZZbR-HK7lRjVgRxRY-E8LpCya3IPwY&_nc_ohc=VwKQk4k0Wr0Q7kNvwFwPF-M&_nc_gid=mh9lLhw0m4Prz8yDpfBoUg&edm=AGenrX8BAAAA&ccb=7-5&oh=00_Af03XEQxNC3G9E8atcofgFRL-0rIduWSsShYTFQNF20Dgg&oe=69E6E703&_nc_sid=ed990e")

    # non event - random meme
    # run_replicate_model("https://scontent-lga3-2.cdninstagram.com/v/t51.82787-15/670920522_18607846543035379_840910838523592951_n.jpg?stp=dst-jpg_e35_p1080x1080_sh0.08_tt6&_nc_ht=scontent-lga3-2.cdninstagram.com&_nc_cat=1&_nc_oc=Q6cZ2gERUo4Dz2-erzujsUEQJXoxWxZxtwN65Ai4t_D7dTVekRZPw1cr2Wj_0PrvfaWOf0o&_nc_ohc=Zr2qNGrgpcgQ7kNvwFt5WF2&_nc_gid=VI80Etol3UpHc0hmI0HmpQ&edm=AGenrX8BAAAA&ccb=7-5&oh=00_Af3laErHyz9T7JrB7GF3WbDcjFgFwdyoZn_iyCEkmA3VgQ&oe=69E700AA&_nc_sid=ed990e")

    # written in brooklyn - no time
    # print(run_replicate_model("https://www.instagram.com/p/DV81hgZDr6h/media/?size=l"))

    # climate cafe
    # print(run_replicate_model("https://www.instagram.com/p/DXFVnf0CQ0A/media/?size=l"))

    # model_results = run_replicate_model("https://www.instagram.com/p/DXFVnf0CQ0A/media/?size=l")
    # create_calendar_event("https://www.instagram.com/p/DXFVnf0CQ0A", model_results)

    bookmarks = get_raindrop_bookmarks()
    for id, uri in bookmarks:
        clean_uri = parse.urlunparse(parse.urlparse(uri)._replace(query=""))
        image_uri = f"{clean_uri}media/?size=l"
        print(id, uri, image_uri)

        model_results = run_replicate_model(image_uri)
        create_calendar_event(uri, model_results)
        delete_raindrop_bookmark(id)

        print()
        time.sleep(10)

