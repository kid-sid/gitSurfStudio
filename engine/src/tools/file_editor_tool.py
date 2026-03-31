import os
from typing import Optional

class FileEditorTool:
    """
    Allows the agent to read, write, and modify files.
    Includes safety checks to prevent path traversal outside the root directory.
    """

    def __init__(self, root_path: str):
        self.root_path = os.path.abspath(root_path)

    def _get_abs_path(self, rel_path: str) -> str:
        """Returns the absolute path, ensuring it's within the root_path to prevent path traversal."""
        abs_path = os.path.abspath(os.path.join(self.root_path, rel_path))
        if not abs_path.startswith(self.root_path):
            raise ValueError(f"Path restricted: {rel_path} resolves outside of root directory.")
        return abs_path

    def read_file(self, path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        """Reads a file, optionally by line range (1-indexed)."""
        try:
            abs_path = self._get_abs_path(path)
            if not os.path.exists(abs_path):
                return f"[Error] File not found: {path}"
            
            with open(abs_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                
            if start_line is not None and end_line is not None:
                # 1-indexed to 0-indexed
                target_lines = lines[start_line - 1 : end_line]
                return "".join(target_lines)
            
            return "".join(lines)
        except Exception as e:
            return f"[Error] Expected to read file: {e}"

    def write_file(self, path: str, content: str) -> str:
        """
        Creates a NEW file. To prevent accidental data loss, this method
        FAILS if the file already exists. To modify an existing file,
        you MUST use replace_in_file().
        """
        try:
            abs_path = self._get_abs_path(path)
            if os.path.exists(abs_path):
                return f"[Error] File already exists: {path}. Use replace_in_file() for modifications."

            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            
            # Signal UI that AI is about to write this file
            print(f"[UI_COMMAND] ai_writing_start {abs_path}")

            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Signal UI
            print(f"[UI_COMMAND] file_created {abs_path}")

            line_count = len(content.splitlines())
            return f"[Success] Wrote {path} ({line_count} lines)."

        except Exception as e:
            return f"[Error] Failed to write file: {e}"

    def replace_in_file(
        self,
        path: str,
        target: str,
        replacement: str,
        allow_multiple: bool = False,
    ) -> str:
        """
        Replaces target with replacement in the file.
        By default refuses to proceed if target appears more than once —
        the agent must provide a more specific target string.
        Set allow_multiple=True only for intentional global find-and-replace.
        """ 
        try:
            abs_path = self._get_abs_path(path)
            if not os.path.exists(abs_path):
                return f"[Error] File not found: {path}"

            with open(abs_path, "r", encoding="utf-8") as f:
                original_content = f.read()

            # Guard against accidental full-file replacement that wipes content
            # (e.g., when the agent passes the entire file as the target and a
            # small replacement snippet). Require the agent to use a smaller,
            # specific target in that case to avoid clobbering the whole file.
            if target.strip() == original_content.strip():
                return (
                    "[Error] Replacement would overwrite the entire file. "
                    "Provide a smaller, unique target segment to replace so the rest "
                    "of the file is preserved."
                )

            # ── Occurrence check ─────────────────────────────────────────
            count = original_content.count(target)

            if count == 0:
                return (
                    f"[Error] Target string not found in {path}. "
                    f"The file may have changed since it was read. "
                    f"Use read_file() to get the current content before retrying."
                )

            if count > 1 and not allow_multiple:
                # Show the agent exactly where the matches are so it can
                # construct a more specific target on the next attempt
                lines = original_content.splitlines()
                match_lines = [
                    f"  line {i+1}: {line.strip()}"
                    for i, line in enumerate(lines)
                    if target in line
                ]
                match_preview = "\n".join(match_lines[:5])
                if count > 5:
                    match_preview += f"\n  ... and {count - 5} more"

                return (
                    f"[Error] Target string appears {count} times in {path}. "
                    f"Provide a more specific target that matches exactly once.\n"
                    f"Matches found at:\n{match_preview}"
                )
            # ── Backup before mutating ───────────────────────────────────
            backup_result = self._write_backup(abs_path, original_content)
            if backup_result:
                print(f"   [FileEditor] Backup created: {backup_result}")

            new_content = original_content.replace(target, replacement) if allow_multiple else original_content.replace(target, replacement, 1)

            print(f"[UI_COMMAND] ai_writing_start {abs_path}")
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"[UI_COMMAND] file_changed {abs_path}")

            lines_changed = abs(
                len(new_content.splitlines()) - len(original_content.splitlines())
            )
            return (
                f"[Success] Replaced content in {path}. "
                f"Lines delta: {lines_changed:+d}. "
                f"Backup saved to {backup_result}."
            )

        except Exception as e:
            return f"[Error] Failed to replace in file: {e}"

    def multi_replace_file_content(
        self,
        rel_path: str,
        replacement_chunks: list
    ) -> str:
        """
        Replaces multiple non-contiguous chunks in a file.
        replacement_chunks: list of dicts with 'targetContent' and 'replacementContent'
        """
        try:
            abs_path = self._get_abs_path(rel_path)
            if not os.path.exists(abs_path):
                return f"[Error] File not found: {rel_path}"

            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Verify all targets exist EXACTLY once before making any changes
            for i, chunk in enumerate(replacement_chunks):
                target = chunk.get("targetContent", chunk.get("TargetContent", ""))
                count = content.count(target)
                if count == 0:
                    return f"[Error] Target {i+1} not found in {rel_path}."
                if count > 1:
                    return f"[Error] Target {i+1} appears {count} times in {rel_path}. Provide a more specific target."

            backup_result = self._write_backup(abs_path, content)
            if backup_result:
                print(f"   [FileEditor] Backup created: {backup_result}")

            new_content = content
            for chunk in replacement_chunks:
                target = chunk.get("targetContent", chunk.get("TargetContent", ""))
                replacement = chunk.get("replacementContent", chunk.get("ReplacementContent", ""))
                new_content = new_content.replace(target, replacement, 1)

            print(f"[UI_COMMAND] ai_writing_start {abs_path}")
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"[UI_COMMAND] file_changed {abs_path}")

            lines_changed = abs(len(new_content.splitlines()) - len(content.splitlines()))
            return f"[Success] Multi-replaced {len(replacement_chunks)} chunks in {rel_path}. Lines delta: {lines_changed:+d}."

        except Exception as e:
            return f"[Error] Failed to multi-replace in file: {e}"

    def _write_backup(self, abs_path: str, content: str) -> Optional[str]:
        """
        Writes a .bak copy of the file before mutation.
        Stored alongside the original — e.g. foo.py → foo.py.bak
        Returns the backup path string, or None if backup failed.
        """
        try:
            backup_path = abs_path + ".bak"
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write(content)
            return backup_path
        except Exception as e:
            print(f"   [FileEditor] Warning: Could not create backup: {e}")
            return None

    def delete_file(self, path: str) -> str:
        """Deletes a file from the file system."""
        try:
            abs_path = self._get_abs_path(path)
            if not os.path.exists(abs_path):
                return f"[Error] File not found: {path}"
                
            os.remove(abs_path)
            return f"[Success] Deleted file: {path}"
        except Exception as e:
            return f"[Error] Failed to delete file: {e}"

if __name__ == "__main__":
    # Simple internal test
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    tool = FileEditorTool(base_dir)
    test_file = "test_editor.txt"
    
    print(tool.write_file(test_file, "Hello World\nLine 2\nLine 3\n"))
    print(tool.read_file(test_file))
    print(tool.replace_in_file(test_file, "World", "Agent"))
    print(tool.read_file(test_file))
    
    # Cleanup
    try:
        os.remove(os.path.join(base_dir, test_file))
        print(f"[Cleanup] Removed {test_file}")
    except:
        pass
