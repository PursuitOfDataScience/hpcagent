import os


def read_document(file_path: str, base_path: str = None) -> str:
    if base_path is None:
        return "Error: base_path is required. Set it to your documentation root."
    full_path = os.path.join(base_path, file_path)
    try:
        with open(full_path, encoding='utf-8') as f:
            content = f.read()
        if len(content) > 15000:
            content = content[:15000] + "\n\n[... Document truncated due to length ...]"
        return content
    except FileNotFoundError:
        return f"Error: Document '{file_path}' not found."
    except Exception as e:
        return f"Error reading document: {str(e)}"
