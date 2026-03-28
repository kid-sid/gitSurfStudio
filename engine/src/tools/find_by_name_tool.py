import os
from pathlib import Path
from typing import List

class FindByNameTool:
    """
    Search for files and subdirectories within the workspace using glob patterns.
    """

    def __init__(self, workspace_path: str):
        self.workspace_path = os.path.abspath(workspace_path)

    def find_by_name(
        self,
        pattern: str,
        type: str = "any",
        max_depth: int = None,
        full_path: bool = False
    ) -> List[str]:
        """
        Search for files using glob patterns.
        
        Args:
            pattern: Glob pattern to search for (e.g., '*.py', 'src/*.js')
            type: Type filter ('file', 'directory', 'any')
            max_depth: Optional maximum depth to search (not fully strict in this simple implementation, typically handled by ** nesting)
            full_path: If true, pattern matches against the full relative path instead of just the filename.
        """
        root = Path(self.workspace_path)
        
        try:
            # If it's a full path search or implies depth, we just rglob
            if not pattern.startswith("**/"):
                search_pattern = f"**/{pattern}" if not full_path else pattern
            else:
                search_pattern = pattern
                
            matches = root.rglob(search_pattern) if "**" in search_pattern else root.glob(search_pattern)
            
            results = []
            for m in matches:
                # Type filtering
                if type == "file" and not m.is_file():
                    continue
                if type == "directory" and not m.is_dir():
                    continue
                    
                try:
                    rel_path = m.relative_to(root).as_posix()
                    
                    # Basic depth filtering
                    if max_depth is not None:
                        if len(Path(rel_path).parts) > max_depth:
                            continue
                            
                    results.append(rel_path)
                except ValueError:
                    continue
            
            return sorted(results)
            
        except Exception as e:
            return [f"[Error] FindByName failed: {e}"]
