import asyncio
import socket 
import threading 
from http.server import BaseHTTPRequestHandler, HTTPServer
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
import mcp.types as types

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

server = Server("brochure-generator")

@server.list_tools()
async def handle_list_tools():
    return [
        types.Tool(
            name="generate_brochure",
            description="Generates a Hello World brochure and returns a localhost URL.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city_name": {"type": "string", "description": "The name of the city"}
                },
                "required": ["city_name"]
            },
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    if name == "generate_brochure":
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
