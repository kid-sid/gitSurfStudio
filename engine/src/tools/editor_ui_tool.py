import os

class EditorUITool:
    """
    A tool that allows the agent to trigger UI actions in the GitSurf Studio frontend.
    These commands are intercepted by the server and sent via SSE.
    """
    def __init__(self, root_path: str):
        self.root_path = os.path.abspath(root_path)

    def open_file(self, rel_path: str) -> str:
        """
        Opens a file in the IDE editor.
        :param rel_path: Relative path to the file from workspace root.
        """
        abs_path = os.path.join(self.root_path, rel_path)
        if not os.path.exists(abs_path):
            return f"[Error] File not found: {rel_path}"
        
        # We print a special marker that the server will intercept
        # The frontend uses absolute paths for tab management
        print(f"[UI_COMMAND] open_file {abs_path}")
        return f"Successfully requested UI to open {rel_path}"
