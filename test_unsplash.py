import requests
import os
from dotenv import load_dotenv

load_dotenv()

access_key = os.getenv("UNSPLASH_ACCESS_KEY")

response = requests.get(
    "https://api.unsplash.com/search/photos",
    params={"query": "Portland skyline", "per_page": 5, "orientation": "landscape"},
    headers={"Authorization": f"Client-ID {access_key}"}
)

data = response.json()
for photo in data["results"]:
    print(photo["urls"]["regular"])