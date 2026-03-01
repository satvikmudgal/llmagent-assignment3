import asyncio
import socket 
import threading 
import requests
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
import mcp.types as types
from dotenv import load_dotenv

load_dotenv()

server = Server("city-data-service")

HTML_CONTENT = """
<!DOCTYPE html>
<html>
<head><title>Online Brochure</title></head>
<body>
    <h1>Hello World!</h1>
    <p>This is your generated city brochure.</p>
</body>
</html>
"""

@server.list_tools()
async def handle_list_tools():
    return [
        types.Tool(
            name="generate_brochure",
            description="Generates a Hello World brochure and returns a localhost URL.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city_name": {"type": "string", "description": "The name of the city"},
                    "query_type": {
                        "type": "string",
                        "enum": ["skyline", "landmark", "attraction", "general", "city", "nature"],
                        "description": "Optional. Type of images to feature. Defaults to general city images if not specified."
                    }
                },
                "required": ["city_name"]
            },
        ),
        # types.Tool(
        #     name="get_tourist_attraction",
        #     description="Returns a list of attractions with addresses and hours of operation",
        #     inputSchema={
        #         "type": "object",
        #         "properties": {
        #             "city": {"type": "string"},
        #         },
        #         "required": ["city"]
        #     },
        # ),
        # types.Tool(
        #     name="get_restaurants_by_cuisine",
        #     description="Returns top-rated restaurants grouped by cuisine type.",
        #     inputSchema={
        #         "type": "object",
        #         "properties": {
        #             "city": {"type": "string"},
        #             "cuisines": {"type": "array", "items": {"type": "string"}}
        #         },
        #         "required": ["city", "cuisines"]
        #     },
        # ),
        # types.Tool(
        #     name="get_weather_widget",
        #     description="Fetches current weather and returns a pre-formatted HTML widget",
        #     inputSchema={
        #         "type": "object",
        #         "properties": {
        #             "city": {"type": "string"},
        #         },
        #         "required": ["city"]
        #     },
        # ),

    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    if not arguments:
        raise ValueError("Missing Arguments")

    if  name == "generate_brochure":
        city_name = arguments.get("city_name")
        query_type = arguments.get("query_type", "city")
        
        image_urls = fetch_unsplash_images(city_name, query_type)
        weather = fetch_nws_weather(city_name)
        if "error" in weather:
            weather = {"temperature": "N/A", "conditions": "Unavailable", "wind": "N/A", "alerts": []}

        html = build_brochure_html(city_name, image_urls, weather)

        global HTML_CONTENT
        HTML_CONTENT = html 
        port = find_open_port()
        start_web_server(port)
        url = f"http://localhost:{port}"

        return [
            types.TextContent(
                type="text",
                text=f"Brochure generated successfully. Access it here: {url}"
            )
        ]
    raise ValueError(f"Tool not found: {name}")

class BrochureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(HTML_CONTENT.encode("utf-8"))

    def log_message(self, format, *args):
        return
    
def find_open_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]
    
def start_web_server(port):
    server = HTTPServer(("localhost", port), BrochureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return port

def fetch_unsplash_images(city: str, query_type: str = "city", count: int = 6) -> list[str]:
    access_key = os.getenv("UNSPLASH_ACCESS_KEY")
    response = requests.get(
        "https://api.unsplash.com/search/photos",
        params={"query": f"{city} {query_type}", "per_page": count, "orientation": "landscape"},
        headers={"Authorization": f"Client-ID {access_key}"}
    )
    data = response.json()
    return [photo["urls"]["regular"] for photo in data.get("results", [])]

def fetch_nws_weather(city: str) -> dict:
    """
    Fetches weather data from the National Weather Service API.
    Requires a geocoding step first to convert city name to latitude and longitude,
    then NWS grid coordinates, then the forecast.
    """

    headers = {"User-Agent": "city-brochure-app/1.0"}

    geo_response = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": city, "format": "json", "limit": 1},
        headers=headers
    )
    geo_data = geo_response.json()
    if not geo_data:
        return {"error": f"Could not geocode city: {city}"}
    
    lat = float(geo_data[0]["lat"])
    lon = float(geo_data[0]["lon"])

    points_response = requests.get(
        f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}",
        headers=headers
    )

    points_data = points_response.json()
    if "properties" not in points_data:
        return {"error": "Could not retrieve NWS grid point."}
    
    forecast_url = points_data["properties"]["forecast"]
    alerts_zone = points_data["properties"]["county"]

    forecast_response = requests.get(forecast_url, headers=headers)
    forecast_data = forecast_response.json()
    periods = forecast_data["properties"]["periods"]
    current = periods[0]

    zone_id = alerts_zone.split("/")[-1]
    alerts_response = requests.get(
        f"https://api.weather.gov/alerts/active?zone={zone_id}",
        headers=headers
    )
    alerts_data = alerts_response.json()
    alerts = [
        feature["properties"]["headline"] for feature in alerts_data.get("features", [])
    ]

    return {
        "temperature": f"{current['temperature']} degrees {current['temperatureUnit']}",
        "conditions": current["shortForecast"],
        "wind": current["windSpeed"] + " " + current["windDirection"],
        "alerts": alerts
    }

def build_brochure_html(city: str, image_urls: list[str], weather: dict) -> str:
    count = len(image_urls)

    if count == 1:
        grid_style = "grid-template-columns: 1fr;"
    elif count == 2:
        grid_style = "grid-template-columns: 1fr 1fr;"
    elif count == 3:
        grid_style = "grid-template-columns: 1fr 1fr 1fr;"
    else:
        grid_style = "grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));"
    
    images_html = "".join(
        f'<img src="{url}" style="width:100%; height:220px; object-fit:cover; border-radius:8px;"/>'
        for url in image_urls
    )

    if weather.get("alerts"):
        alerts_html = "".join(
            f'<div style="background:#e74c3c; color:white; padding: 4px 8px; border-radius:4px; margin-top:4px; font-size:0.75em;">⚠ {alert}</div>'
            for alert in weather["alerts"]
        )
    else:
        alerts_html = '<div style="font-size:0.75em; opacity:0.8; margin-top:4px;">No active alerts</div>'

    weather_widget = f"""
    <div style="
        position: absolute;
        top: 20px;
        right: 24px;
        background: rgba(0,0,0,0.45);
        border: 1px solid rgba(255,255,255,0.2);
        border-radius: 12px;
        padding: 14px 18px;
        color: white;
        min-width: 180px;
        backdrop-filter: blur(4px);
    ">
        <div style="font-size:0.8em; text-transform:uppercase; letter-spacing:1px; opacity:0.7;">Current Weather</div>
        <div style="font-size:2em; font-weight:bold; margin: 4px 0;">{weather.get('temperature', 'N/A')}</div>
        <div style="font-size:0.9em;">{weather.get('conditions', 'N/A')}</div>
        <div style="font-size:0.8em; opacity:0.8; margin-top:4px;"> {weather.get('wind', 'N/A')}</div>
        {alerts_html}
    </div>
    """
    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>{city} Travel Brochure</title>
    <style>
        body {{
            font-family: Georgia, serif;
            margin: 0;
            padding: 0;
            background: #fafafa;
            color: #222;
        }}
        .hero {{
            position: relative;
            background: #2c3e50;
            color: white;
            text-align: center;
            padding: 60px 20px;
        }}
        .hero h1 {{
            font-size: 3em;
            margin: 0;
            letter-spacing: 2px;
        }}
        .hero p {{
            font-size: 1.2em;
            opacity: 0.8;
        }}
        .section {{
            max-width: 1100px;
            margin: 40px auto;
            padding: 0 20px;
        }}
        .image-grid {{
            display: grid;
            {grid_style}
            gap: 16px;
            margin-top: 20px;
        }}
        h2 {{
            border-bottom: 2px solid #2c3e50;
            padding-bottom: 8px;
        }}
    </style>
</head>
<body>
    <div class="hero">
        <h1>{city}</h1>
        <p>Discover the beauty and culture of {city}</p>
        {weather_widget}
    </div>
    <div class="section">
        <h2>Gallery</h2>
        <div class="image-grid">
            {images_html}
        </div>
    </div>
</body>
</html>
"""


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="brochure-generator",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
