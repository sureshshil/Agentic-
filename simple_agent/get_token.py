"""One-time Gmail OAuth authorization - run this on your OWN computer,
NOT inside the Codespace.

Why: the Codespace's browser and its Python process run on different
machines, so a local-server OAuth redirect to "localhost" can't complete
there. Running this script on your own machine keeps browser and server
on the same machine, where "localhost" actually works.

Setup:
  1. Put this file in the same folder as your client_secret.json
     (downloaded from Google Cloud Console -> APIs & Services ->
     Credentials -> your OAuth 2.0 Client ID -> Download JSON).
  2. pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
  3. python get_token.py
     This opens your real local browser. Sign in and grant access.
  4. It writes gmail_token.json next to this script. Upload THAT file
     (not client_secret.json) into simple_agent/notebooks/ in your
     Codespace - drag it into the file explorer, or right-click the
     notebooks folder -> Upload.
  5. Re-run the OAuth cell in 06_gmail_oauth.ipynb. It checks for
     gmail_token.json first and will use it directly, skipping the
     browser step entirely.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)  # your real local browser, real local port

with open("gmail_token.json", "w") as f:
    f.write(creds.to_json())

print("Saved gmail_token.json - upload this file into simple_agent/notebooks/ in your Codespace.")
