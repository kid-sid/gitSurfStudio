import os
from pathlib import Path
from typing import List

class GlobTool:
    """
    GlobTool: Find files using glob patterns.
    """

    def __init__(self, workspace_path: str):
        self.workspace_path = os.path.abspath(workspace_path)

    def list_files(self, pattern: str, recursive: bool = True) -> List[str]:
        """
        List files matching a glob pattern.
        
        Args:
            pattern: Glob pattern (e.g., "**/*.py", "src/*.js")
            recursive: Whether to search recursively (ignored if pattern already uses **/)
            
        Returns:
            List of matching file paths relative to the workspace root.
        """
        # Ensure we're using Path for easier globbing
        root = Path(self.workspace_path)
        
        # pathlib's glob handles recursive patterns if they contain **
        # If recursive is True and no **/ is present, we might want to prepend it 
        # but usually the user specifies it in the pattern.
        
        try:
            # We use rglob if recursive is True, otherwise glob
            if recursive and not pattern.startswith("**/"):
                matches = root.rglob(pattern)
            else:
                matches = root.glob(pattern)
            
            # Convert to relative paths and filter out directories
            results = []
            for m in matches:
                if m.is_file():
                    try:
                        rel_path = m.relative_to(root).as_posix()
                        results.append(rel_path)
                    except ValueError:
                        # Path is not relative to root (shouldn't happen with rglob/glob from root)
                        pass
            
            return sorted(results)
            
        except Exception as e:
            return [f"[Error] Glob failed: {e}"]
