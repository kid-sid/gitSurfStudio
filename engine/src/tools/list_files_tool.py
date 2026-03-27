import os
from typing import List, Dict, Any

class ListFilesTool:
    """
    ListFilesTool: Structured directory listing for the agent.
    """

    def __init__(self, workspace_path: str):
        self.workspace_path = os.path.abspath(workspace_path)

    def list_dir(self, rel_path: str = ".") -> List[Dict[str, Any]]:
        """
        List files and folders in a specific directory.
        
        Args:
            rel_path: Path relative to the workspace root.
            
        Returns:
            List of objects containing name, is_dir, and size (if file).
        """
        abs_path = os.path.abspath(os.path.join(self.workspace_path, rel_path))
        
        # Security: ensure path is within workspace
        if not abs_path.startswith(self.workspace_path):
            return [{"error": "Path is outside of workspace"}]

        if not os.path.exists(abs_path):
            return [{"error": "Path does not exist"}]

        if not os.path.isdir(abs_path):
            return [{"error": "Path is not a directory"}]

        results = []
        try:
            for entry in os.scandir(abs_path):
                info = {
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                }
                if entry.is_file():
                    info["size"] = entry.stat().st_size
                results.append(info)
            
            # Sort: directories first, then alphabetically
            return sorted(results, key=lambda x: (not x["is_dir"], x["name"].lower()))
            
        except Exception as e:
            return [{"error": str(e)}]

    def list_recursive(self, rel_path: str = ".") -> List[str]:
        """
        List all files recursively under a path.
        
        Args:
            rel_path: Path relative to the workspace root.
            
        Returns:
            List of relative file paths.
        """
        abs_start = os.path.abspath(os.path.join(self.workspace_path, rel_path))
        
        # Security: ensure path is within workspace
        if not abs_start.startswith(self.workspace_path):
            return ["[Error] Path is outside of workspace"]

        if not os.path.exists(abs_start):
            return ["[Error] Path does not exist"]

        results = []
        try:
            for root, dirs, files in os.walk(abs_start):
                # Skip common ignore dirs to prevent bloat
                dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".cache", "venv", ".venv"}]
                
                for file in files:
                    abs_file = os.path.join(root, file)
                    rel_file = os.path.relpath(abs_file, self.workspace_path).replace("\\", "/")
                    results.append(rel_file)
            
            return sorted(results)
            
        except Exception as e:
            return [f"[Error] Recursive list failed: {e}"]
