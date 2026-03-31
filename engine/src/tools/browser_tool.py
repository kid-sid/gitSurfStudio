"""
BrowserTool — High-level browser automation wrapper over Playwright MCP.

Provides semantic methods (verify_page, test_interaction, debug_page, scrape_rendered)
that internally orchestrate multiple Playwright MCP calls in a single tool invocation,
saving ReAct iterations for the agent.
"""

import json
import time
from typing import Any, Callable, Dict, List, Optional


class BrowserTool:
    def __init__(self, tools_getter: Callable[[], Dict[str, Any]]):
        """
        Args:
            tools_getter: callable returning the current agent_tools dict.
                          Lazy so MCP proxies (loaded in background) are resolved at call time.
        """
        self._get_tools = tools_getter

    # Internal helpers

    def _call_mcp(self, tool_suffix: str, **kwargs) -> str:
        """Call mcp__playwright__<tool_suffix>.execute(**kwargs) and return result."""
        tools = self._get_tools()
        full_key = f"mcp__playwright__{tool_suffix}"
        shorthand = tool_suffix.replace("-", "_")

        proxy = tools.get(full_key) or tools.get(shorthand)
        if proxy is None:
            return f"[Error] Playwright tool '{tool_suffix}' not found. MCP may not be ready."

        try:
            return proxy.execute(**kwargs)
        except Exception as e:
            return f"[Error] {tool_suffix} raised: {e}"

    def _is_error(self, result: str) -> bool:
        return str(result).startswith("[Error]")

    def _truncate_screenshot(self, result: str, label: str = "Screenshot") -> str:
        """Replace raw base64 screenshot data with a short summary."""
        text = str(result)
        if len(text) > 500 and ("base64" in text.lower() or len(text) > 2000):
            return f"[{label} captured — {len(text)} chars of image data]"
        return text

    # ── Public methods ──────────────────────────────────────────────────

    def verify_page(
        self, url: str, checks: Optional[str] = None, wait_ms: int = 2000
    ) -> str:
        """
        Navigate to URL, wait, capture snapshot + screenshot, optionally check
        for expected text strings in the page.

        Args:
            url: Absolute URL to navigate to (e.g. http://localhost:1420)
            checks: JSON array of strings to look for, e.g. '["Submit", "Welcome"]'
            wait_ms: Milliseconds to wait after navigation before capturing (default 2000)
        """
        report_parts: List[str] = [f"## Page Verification: {url}\n"]

        # 1. Navigate
        nav_result = self._call_mcp("browser_navigate", url=url)
        if self._is_error(nav_result):
            return f"[Error] Navigation failed: {nav_result}"

        # 2. Wait for page to settle
        if wait_ms > 0:
            time.sleep(wait_ms / 1000.0)

        # 3. Snapshot (DOM / accessibility tree)
        snapshot = self._call_mcp("browser_snapshot")
        if self._is_error(snapshot):
            report_parts.append(f"### Snapshot\n{snapshot}\n")
        else:
            report_parts.append(f"### Snapshot\n{snapshot}\n")

        # 4. Check for expected strings
        if checks:
            try:
                check_list = json.loads(checks) if isinstance(checks, str) else checks
            except (json.JSONDecodeError, TypeError):
                check_list = [checks] if isinstance(checks, str) else []

            if check_list:
                report_parts.append("### Checks")
                snapshot_lower = str(snapshot).lower()
                for check_str in check_list:
                    found = str(check_str).lower() in snapshot_lower
                    status = "PASS" if found else "FAIL"
                    report_parts.append(f"- [{status}] \"{check_str}\"")
                report_parts.append("")

        # 5. Screenshot
        screenshot = self._call_mcp("browser_screenshot")
        report_parts.append(
            f"### Screenshot\n{self._truncate_screenshot(screenshot)}\n"
        )

        return "\n".join(report_parts)

    def test_interaction(self, url: str, steps: str) -> str:
        """
        Execute a sequence of browser interactions and report pass/fail per step.

        Args:
            url: Starting URL to navigate to
            steps: JSON array of step objects. Each step has:
                   - action: "navigate"|"click"|"type"|"snapshot"|"screenshot"|"wait"
                   - element: (for click/type) element description or ref
                   - text: (for type) text to enter
                   - url: (for navigate) URL override
                   - expect: (for snapshot) text expected in the page
                   - ms: (for wait) milliseconds to wait
                   - ref: (for click/type) element ref from a prior snapshot
        """
        # Parse steps
        try:
            step_list = json.loads(steps) if isinstance(steps, str) else steps
        except (json.JSONDecodeError, TypeError):
            return f"[Error] Could not parse steps JSON: {steps}"

        if not isinstance(step_list, list) or not step_list:
            return "[Error] steps must be a non-empty JSON array"

        report_parts: List[str] = [f"## Interaction Test: {url}\n"]

        # Initial navigation
        nav_result = self._call_mcp("browser_navigate", url=url)
        if self._is_error(nav_result):
            return f"[Error] Initial navigation to {url} failed: {nav_result}"
        report_parts.append(f"- [PASS] Navigate to {url}")
        time.sleep(1)  # brief settle

        for i, step in enumerate(step_list, 1):
            action = step.get("action", "").lower()
            step_label = f"Step {i}: {action}"

            if action == "navigate":
                step_url = step.get("url", url)
                result = self._call_mcp("browser_navigate", url=step_url)
                if self._is_error(result):
                    report_parts.append(f"- [FAIL] {step_label} → {result}")
                    break
                report_parts.append(f"- [PASS] {step_label} → {step_url}")
                time.sleep(0.5)

            elif action == "click":
                element = step.get("element", "")
                kwargs: Dict[str, Any] = {"element": element}
                if "ref" in step:
                    kwargs["ref"] = step["ref"]
                result = self._call_mcp("browser_click", **kwargs)
                if self._is_error(result):
                    report_parts.append(f"- [FAIL] {step_label} '{element}' → {result}")
                    break
                report_parts.append(f"- [PASS] {step_label} '{element}'")
                time.sleep(0.5)

            elif action == "type":
                element = step.get("element", "")
                text = step.get("text", "")
                kwargs = {"element": element, "text": text}
                if "ref" in step:
                    kwargs["ref"] = step["ref"]
                result = self._call_mcp("browser_type", **kwargs)
                if self._is_error(result):
                    report_parts.append(
                        f"- [FAIL] {step_label} '{element}' ← '{text}' → {result}"
                    )
                    break
                report_parts.append(f"- [PASS] {step_label} '{element}' ← '{text}'")

            elif action == "snapshot":
                result = self._call_mcp("browser_snapshot")
                expect = step.get("expect")
                if self._is_error(result):
                    report_parts.append(f"- [FAIL] {step_label} → {result}")
                    break
                if expect and str(expect).lower() not in str(result).lower():
                    report_parts.append(
                        f"- [FAIL] {step_label} — expected \"{expect}\" not found in snapshot"
                    )
                    report_parts.append(f"  Snapshot: {str(result)[:500]}")
                    break
                status = "PASS" if expect else "INFO"
                msg = f" — found \"{expect}\"" if expect else ""
                report_parts.append(f"- [{status}] {step_label}{msg}")

            elif action == "screenshot":
                result = self._call_mcp("browser_screenshot")
                report_parts.append(
                    f"- [INFO] {step_label} → {self._truncate_screenshot(result)}"
                )

            elif action == "wait":
                ms = step.get("ms", 1000)
                time.sleep(int(ms) / 1000.0)
                report_parts.append(f"- [PASS] {step_label} {ms}ms")

            else:
                report_parts.append(f"- [SKIP] {step_label} — unknown action '{action}'")

        return "\n".join(report_parts)

    def debug_page(self, url: str) -> str:
        """
        Navigate to URL and capture snapshot, screenshot, and console messages
        for debugging client-side issues.

        Args:
            url: Absolute URL to debug (e.g. http://localhost:1420)
        """
        report_parts: List[str] = [f"## Debug Report: {url}\n"]

        # 1. Navigate
        nav_result = self._call_mcp("browser_navigate", url=url)
        if self._is_error(nav_result):
            return f"[Error] Navigation failed: {nav_result}"
        time.sleep(2)  # let page fully load

        # 2. DOM Snapshot
        snapshot = self._call_mcp("browser_snapshot")
        report_parts.append(f"### DOM Snapshot\n{snapshot}\n")

        # 3. Screenshot
        screenshot = self._call_mcp("browser_screenshot")
        report_parts.append(
            f"### Screenshot\n{self._truncate_screenshot(screenshot)}\n"
        )

        # 4. Console messages (graceful — may not exist in all Playwright MCP versions)
        console = self._call_mcp("browser_console_messages")
        if self._is_error(console) and "not found" in console.lower():
            report_parts.append(
                "### Console\n[Console capture not available in this Playwright MCP version]\n"
            )
        else:
            report_parts.append(f"### Console Messages\n{console}\n")

        return "\n".join(report_parts)

    def scrape_rendered(self, url: str) -> str:
        """
        Fetch content from a JavaScript-rendered page. Use this instead of
        WebSearchTool.fetch_url when the target is a SPA or requires JS execution.

        Args:
            url: Absolute URL to scrape
        """
        # 1. Navigate
        nav_result = self._call_mcp("browser_navigate", url=url)
        if self._is_error(nav_result):
            return f"[Error] Navigation failed: {nav_result}"
        time.sleep(2)  # let JS render

        # 2. Snapshot
        snapshot = self._call_mcp("browser_snapshot")
        if self._is_error(snapshot):
            return f"[Error] Could not capture page content: {snapshot}"

        return f"## Rendered Content: {url}\n\n{snapshot}"
