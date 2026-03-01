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
                    },
                    "cuisine": {
                        "type": "string",
                        "description": "Optional. Filter restaurants by cuisine type e.g. italian, chinese, mexican, japanese."
                    },
                    "attraction_type": {
                        "type": "string",
                        "description": "Optional. Filter attractions by type e.g. museum, gallery, viewpoint, artwork, hotel."
                    }
                },
                "required": ["city_name"]
            },
        ),
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    if not arguments:
        raise ValueError("Missing Arguments")

    if  name == "generate_brochure":
        city_name = arguments.get("city_name")
        query_type = arguments.get("query_type", "city")
        cuisine = arguments.get("cuisine", None)
        attraction_type = arguments.get("attraction_type", None)
        
        image_urls = fetch_unsplash_images(city_name, query_type)
        weather = fetch_nws_weather(city_name)
        if "error" in weather:
            weather = {"temperature": "N/A", "conditions": "Unavailable", "wind": "N/A", "alerts": []}

        restaurants = fetch_restaurants(city_name, cuisine)
        attractions = fetch_tourist_attractions(city_name, attraction_type)

        #html = build_brochure_html(city_name, image_urls, weather, restaurants, attractions)

        data_package = {
            "images": image_urls,
            "weather": weather,
            "restaurants": restaurants,
            "attractions": attractions
        }
        html = generate_dynamic_html(city_name, data_package)

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

def fetch_restaurants(city: str, cuisine: str = None, count: int = 8) -> list[dict]:
    """
    Fetches restaurants from OpenStreetMap via the Overpass API
    Optionally filters by cuisine type.
    """

    geo_response = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": city, "format": "json", "limit": 1},
        headers={"User-Agent": "city-brochure-app/1.0"}
    )
    geo_data = geo_response.json()
    if not geo_data:
        return []
    
    bbox = geo_data[0]["boundingbox"]
    south, north, west, east = bbox

    cuisine_filter = f'["cuisine"="{cuisine.lower()}"]' if cuisine else '["cuisine"]'

    overpass_query = f"""
    [out:json];
    node["amenity"="restaurant"]{cuisine_filter}
    ({south},{west},{north},{east});
    out {count};
    """

    overpass_response = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": overpass_query},
        headers={"User-Agent": "city-brochure-app/1.0"}
    )
    overpass_data = overpass_response.json()

    restaurants = []
    for element in overpass_data.get("elements", []):
        tags = element.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        restaurants.append({
            "name": name,
            "cuisine": tags.get("cuisine", "Various").replace(";", ", ").title(),
            "address": tags.get("addr:street", "") + " " + tags.get("addr:housenumber", ""),
            "phone": tags.get("phone", ""),
            "website": tags.get("website", ""),
            "opening_hours": tags.get("opening_hours", "")
        })

    return restaurants

def fetch_tourist_attractions(city: str, attraction_type: str = None, count: int = 8) -> list[dict]:
    """Fetches tourist attractions from OpenStreetMap via the Overpass API.
    Optionally filters by attraction type.
    """
    geo_response = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": city, "format": "json", "limit": 1},
        headers={"User-Agent": "city-brochure-app/1.0"}
    )
    geo_data = geo_response.json()
    if not geo_data:
        return []

    bbox = geo_data[0]["boundingbox"]
    south, north, west, east = bbox

    type_filter = f'["tourism"="{attraction_type.lower()}"]' if attraction_type else '["tourism"]'

    overpass_query = f"""
    [out:json];
    node{type_filter}
    ({south},{west},{north},{east});
    out {count};
    """

    overpass_response = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": overpass_query},
        headers={"User-Agent": "city-brochure-app/1.0"}
    )
    overpass_data = overpass_response.json()

    attractions = []
    for element in overpass_data.get("elements", []):
        tags = element.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        attractions.append({
            "name": name,
            "type": tags.get("tourism", "Attraction").replace("_", " ").title(),
            "address": (tags.get("addr:street", "") + " " + tags.get("addr:housenumber", "")).strip(),
            "description": tags.get("description", ""),
            "website": tags.get("website", ""),
            "opening_hours": tags.get("opening_hours", "")
        })

    return attractions

def generate_dynamic_html(city, data_package):
    """Uses Gemini to turn raw data into a presentable HTML string."""
    from google import genai

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    prompt = f"""
    You are an expert Frontend Web Designer.
    Task: Create a stunning, HTML/CSS travel brochure for {city}.

    Data to include:
    {data_package}

    Requirements:
    - Use modern, responsive CSS (Flexbox/Grid).
    - Include a high-end 'hero' section using the first Unsplash image.
    - Create distinct sections for the Image Gallery, Weather, Attractions, and Restaurants.
    - Use a color palette that fits the 'vibe' of the city.
    - Return ONLY the raw HTML code. No markdown code blocks.
    """
    
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    html = response.text.strip()

    # Strip markdown code fences if Gemini wraps the output anyway
    if html.startswith("```"):
        html = html.split("\n", 1)[1]
    if html.endswith("```"):
        html = html.rsplit("```", 1)[0]
        
    return response.text

# def build_brochure_html(city: str, image_urls: list[str], weather: dict, restaurants: list[dict], attractions: list[dict]) -> str:
#     count = len(image_urls)

#     if count == 1:
#         grid_style = "grid-template-columns: 1fr;"
#     elif count == 2:
#         grid_style = "grid-template-columns: 1fr 1fr;"
#     elif count == 3:
#         grid_style = "grid-template-columns: 1fr 1fr 1fr;"
#     else:
#         grid_style = "grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));"
    
#     images_html = "".join(
#         f'<img src="{url}" style="width:100%; height:220px; object-fit:cover; border-radius:8px;"/>'
#         for url in image_urls
#     )

#     if weather.get("alerts"):
#         alerts_html = " | ".join(
#             f'<span style="color:#e74c3c;">⚠ {alert}</span>'
#             for alert in weather["alerts"]
#         )
#     else:
#         alerts_html = '<span style="opacity:0.7;">None</span>'

#     weather_widget = f"""
#     <div style="
#         width: 100%;
#         background: #2c3e50;
#         border-top: 1px solid rgba(255,255,255,0.1);
#         border-bottom: 1px solid rgba(255,255,255,0.1);
#         color: white;
#         display: flex;
#         justify-content: center;
#         align-items: center;
#         gap: 48px;
#         padding: 18px 40px;
#         box-sizing: border-box;
#         flex-wrap: wrap;
#     ">
#         <div style="text-align:center;">
#             <div style="font-size:0.7em; text-transform:uppercase; letter-spacing:1px; opacity:0.6;">Temperature</div>
#             <div style="font-size:1.6em; font-weight:bold;">{weather.get('temperature', 'N/A')}</div>
#         </div>
#         <div style="text-align:center;">
#             <div style="font-size:0.7em; text-transform:uppercase; letter-spacing:1px; opacity:0.6;">Conditions</div>
#             <div style="font-size:1.1em;">{weather.get('conditions', 'N/A')}</div>
#         </div>
#         <div style="text-align:center;">
#             <div style="font-size:0.7em; text-transform:uppercase; letter-spacing:1px; opacity:0.6;">Wind</div>
#             <div style="font-size:1.1em;">{weather.get('wind', 'N/A')}</div>
#         </div>
#         <div style="text-align:center;">
#             <div style="font-size:0.7em; text-transform:uppercase; letter-spacing:1px; opacity:0.6;">Alerts</div>
#             <div style="font-size:0.9em;">{alerts_html}</div>
#         </div>
#     </div>
#     """

#     if restaurants:
#         restaurant_cards = "".join(f"""
#         <div style="
#             background: white;
#             border-radius: 10px;
#             box-shadow: 0 2px 8px rgba(0,0,0,0.08);
#             padding: 20px;
#             display: flex;
#             flex-direction: column;
#             gap: 6px;
#         ">
#             <div style="font-size:1.1em; font-weight:bold; color:#2c3e50;">{r['name']}</div>
#             <div style="font-size:0.85em; color:#e67e22; font-style:italic;">{r['cuisine']}</div>
#             {'<div style="font-size:0.82em; color:#666;">' + r['address'].strip() + '</div>' if r['address'].strip() else ''}
#             {'<div style="font-size:0.82em; color:#666;"> ' + r['phone'] + '</div>' if r['phone'] else ''}
#             {'<div style="font-size:0.82em; color:#666;"> ' + r['opening_hours'] + '</div>' if r['opening_hours'] else ''}
#             {'<div style="font-size:0.82em;"><a href="' + r['website'] + '" style="color:#2980b9;" target="_blank"> Website</a></div>' if r['website'] else ''}
#         </div>
#         """ for r in restaurants)

#         restaurants_section = f"""
#         <div class="section">
#             <h2>Where to Eat</h2>
#             <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:16px; margin-top:20px;">
#                 {restaurant_cards}
#             </div>
#         </div>
#         """
#     else:
#         restaurants_section = ""

#     if attractions:
#         attraction_cards = "".join(f"""
#         <div style="
#             background: white;
#             border-radius: 10px;
#             box-shadow: 0 2px 8px rgba(0,0,0,0.08);
#             padding: 20px;
#             display: flex;
#             flex-direction: column;
#             gap: 6px;
#         ">
#             <div style="font-size:1.1em; font-weight:bold; color:#2c3e50;">{a['name']}</div>
#             <div style="font-size:0.85em; color:#27ae60; font-style:italic;">{a['type']}</div>
#             {'<div style="font-size:0.82em; color:#666;">' + a['address'] + '</div>' if a['address'] else ''}
#             {'<div style="font-size:0.82em; color:#555;">' + a['description'] + '</div>' if a['description'] else ''}
#             {'<div style="font-size:0.82em; color:#666;">' + a['opening_hours'] + '</div>' if a['opening_hours'] else ''}
#             {'<div style="font-size:0.82em;"><a href="' + a['website'] + '" style="color:#2980b9;" target="_blank">Website</a></div>' if a['website'] else ''}
#         </div>
#         """ for a in attractions)

#         attractions_section = f"""
#         <div class="section">
#             <h2>Things to Do</h2>
#             <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:16px; margin-top:20px;">
#                 {attraction_cards}
#             </div>
#         </div>
#         """
#     else:
#         attractions_section = ""


#     return f"""
#         <!DOCTYPE html>
#         <html>
#         <head>
#             <title>{city} Travel Brochure</title>
#             <style>
#                 body {{
#                     font-family: Georgia, serif;
#                     margin: 0;
#                     padding: 0;
#                     background: #fafafa;
#                     color: #222;
#                 }}
#                 .hero {{
#                     position: relative;
#                     background: #2c3e50;
#                     color: white;
#                     text-align: center;
#                     padding: 60px 20px;
#                 }}
#                 .hero h1 {{
#                     font-size: 3em;
#                     margin: 0;
#                     letter-spacing: 2px;
#                 }}
#                 .hero p {{
#                     font-size: 1.2em;
#                     opacity: 0.8;
#                 }}
#                 .section {{
#                     max-width: 1100px;
#                     margin: 40px auto;
#                     padding: 0 20px;
#                 }}
#                 .image-grid {{
#                     display: grid;
#                     {grid_style}
#                     gap: 16px;
#                     margin-top: 20px;
#                 }}
#                 h2 {{
#                     border-bottom: 2px solid #2c3e50;
#                     padding-bottom: 8px;
#                 }}
#             </style>
#         </head>
#         <body>
#             <div class="hero">
#                 <h1>{city}</h1>
#                 <p>Discover the beauty and culture of {city}</p>
#             </div>
#             {weather_widget}

#             <div class="section">
#                 <h2>Gallery</h2>
#                 <div class="image-grid">
#                     {images_html}
#                 </div>
#             </div>
#             {attractions_section}
#             {restaurants_section}
#         </body>
#         </html>
#         """


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
