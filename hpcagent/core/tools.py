

class ToolRegistry:
    """Register named tools with JSON schemas and handler functions, then dispatch."""

    def __init__(self):
        self._tools = {}
        self._handlers = {}

    def register(self, name: str, description: str, input_schema: dict, handler: callable):
        self._tools[name] = {
            "name": name,
            "description": description,
            "input_schema": input_schema,
        }
        self._handlers[name] = handler

    def unregister(self, name: str):
        self._tools.pop(name, None)
        self._handlers.pop(name, None)

    def get_schemas(self) -> list:
        return [dict(v) for v in self._tools.values()]

    def get_openai_tools(self) -> list:
        result = []
        for tool in self._tools.values():
            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            })
        return result

    def get_llama_tools(self) -> list:
        result = []
        for t in self._tools.values():
            result.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "strict": True,
                    "parameters": t["input_schema"],
                },
            })
        return result

    def has(self, name: str) -> bool:
        return name in self._tools

    def execute(self, name: str, inp: dict) -> str:
        handler = self._handlers.get(name)
        if handler:
            try:
                return handler(inp)
            except Exception as e:
                return f"Error executing {name}: {e}"
        return f"Unknown tool: {name}"

    def __len__(self):
        return len(self._tools)

    def __contains__(self, name):
        return name in self._tools

    def __repr__(self):
        return f"ToolRegistry({len(self)} tools)"
