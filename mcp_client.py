import asyncio
import os
import json
import sys 
import re

from typing import Optional
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from google import genai
from google.genai import types
from google.genai.types import Tool, FunctionDeclaration
from google.genai.types import GenerateContentConfig

from dotenv import load_dotenv

load_dotenv()

"""
This class when called will be responsible for the following:
    1. Initializing the MCP client and configure the Gemini API.
    2. This will allow the client to connect to the servers.
    3. It will also look for the API key and connect to the GeminiAPI.
    4. It will also manage the asynchronous resource cleanup on exit using AsyncExitStack()
    5. It will also process the user query, initialize the LLM, and send the user input to the LLM
"""
class MCPClient:
    def __init__(self):
        """Initialize the MCP client and the Gemini API."""
        self.function_declarations = []
        self.tool_session_map = {}
        self.exit_stack = AsyncExitStack()

        gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("API Key Not Found.")

        self.genai_client = genai.Client(api_key=gemini_api_key)

    async def connect_to_server(self, server_script_path: str):
        """Connect to the a single server and list available tools."""

        # Acceptable server parameters need to be Python or Javascript files
        command = "python" if server_script_path.endswith('.py') else "node"

        # Define the required parameters for connecting to the given MCP server
        server_params = StdioServerParameters(command=command, args=[server_script_path])

        # Establish communication with the server using stdio, can optionally be done using https.
        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))

        stdio, write = stdio_transport

        session = await self.exit_stack.enter_async_context(ClientSession(stdio, write))

        # Initialize a session with the MCP Server
        await session.initialize()

        # Request available tools from the Server
        response = await session.list_tools()
        tools = response.tools

        print("\nConnected to the server with tools:", [tool.name for tool in tools])

        for tool in tools:
            self.tool_session_map[tool.name] = session

        self.function_declarations.extend(convert_mcp_tools_to_gemini_format(tools))

    async def connect_to_all_servers(self, server_paths: list[str]):
        for path in server_paths:
            await self.connect_to_server(path)
        print(f"\nAll servers connected. Total tools available: {list(self.tool_session_map.keys())}")

    async def process_query(self, query: str) -> str:
        contents = [
            types.Content(role='user', parts=[types.Part.from_text(text=query)])
        ]

        localhost_url = None
        final_text = []

        while True:
            response = self.genai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=types.GenerateContentConfig(tools=self.function_declarations),
            )

            candidate = response.candidates[0]
            has_tool_call = False

            for part in candidate.content.parts:
                if part.function_call:
                    has_tool_call = True 
                    tool_name = part.function_call.name
                    tool_args = part.function_call.args

                    print(f"\n[Gemini Requested Tool Call: {tool_name} with args {tool_args}]")

                    session = self.tool_session_map.get(tool_name)
                    if not session:
                        function_response = {"error": f"No session found for tool: {tool_name}"}
                    else:
                        try:
                            result = await session.call_tool(tool_name, tool_args)
                            function_response = {"result": result.content}
                            result_str = str(result.content)
                            match = re.search(r'http://localhost:\d+[^\s"\']*', result_str)

                            if match:
                                localhost_url = match.group(0)
                        except Exception as e:
                            function_response = {"Tool Excecution Failed": str(e)}

                    contents.append(types.Content(role='model', parts=[part]))
                    contents.append(types.Content(
                        role='user',
                        parts=[types.Part.from_function_response(
                            name=tool_name,
                            response=function_response
                        )]
                    ))
                elif part.text:
                    final_text.append(part.text)

            if not has_tool_call:
                break
        if localhost_url:
            final_text.append(f"\nHere is your requested brochure: {localhost_url}")
        
        return "\n".join(final_text)

        
    
    async def chat_loop(self):
        """Run an interactive chat session in the terminal with this function."""
        print("\nMCP Client Started! Type 'quit' to exit.")

        while True:
            query = input("\nQuery: ").strip()
            if query.lower() == 'quit':
                break
            
            response = await self.process_query(query)
            print("\n" + response)

    async def cleanup(self):
        await self.exit_stack.aclose()



def convert_mcp_tools_to_gemini_format(mcp_tools):
    """
    Converts MCP tool definitions and strips the title field from the recursive
    properties field in the tool json. This is because Gemini returns an error if
    it is passed the title declaration in a tool call.
    This function is specific to using the Gemini API and should be modified if a different
    LLM is chosen to process the user query and to execute the MCP tools.
    
    Args:
        mcp_tools (list): List of MCP tool objects with 'name', 'description' and 'inputSchema'.
    
    Returns:
        list: List of Gemini Tool objects with properly formatted function declarations.
    """

    gemini_tools = []

    for tool in mcp_tools:
        parameters = clean_schema(tool.inputSchema)

        function_declaration = FunctionDeclaration(
            name=tool.name,
            description=tool.description,
            parameters=parameters
        )

        # The Tool function is provided by google-genai to aid in their tool formatting
        gemini_tool = Tool(function_declarations=[function_declaration])
        gemini_tools.append(gemini_tool)
    
    return gemini_tools


def clean_schema(schema):
    """
    This is the function called in convert_mcp_tools_to_gemini_format,
    and is the function that recursively strips the title fields from the JSON schema
    """
    if isinstance(schema, dict):
        schema.pop("title", None)

        if "properties" in schema and isinstance(schema["properties"], dict):
            for key in schema["properties"]:
                schema["properties"][key] = clean_schema(schema["properties"][key])
    
    return schema

async def main():
    if len(sys.argv) < 2:
        print("Usage: python client.py <server1.py> <server2.py> ...")
        sys.exit(1)

    client = MCPClient()
    try:
        await client.connect_to_all_servers(sys.argv[1:])
        await client.chat_loop()
    finally:
        await client.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
