import os
import subprocess
import webbrowser
import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class MacOpenBrowser(webbrowser.BaseBrowser):
    def open(self, url, new=0, autoraise=True):
        subprocess.run(["open", url])
        return True


webbrowser.register("mac-open", None, MacOpenBrowser())


def get_calendar_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True, browser="mac-open")
        with open("token.json", "w") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def main():
    service = get_calendar_service()
    now = datetime.datetime.utcnow().isoformat() + "Z"
    events_result = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=5,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    events = events_result.get("items", [])

    if not events:
        print("다가오는 일정이 없습니다.")
        return

    print("--- 다가오는 일정 5개 ---")
    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        print(start, "|", event.get("summary"))


if __name__ == "__main__":
    main()
