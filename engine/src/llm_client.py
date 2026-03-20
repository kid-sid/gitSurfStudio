from typing import List, Dict
import re
import os
import sys
import json
from openai import OpenAI
from src.prompts import (
    refine_query_prompt,
    identify_relevant_files_prompt,
    generate_search_queries_prompt,
    analyze_project_context_prompt,
    generate_questions_prompt,
    github_search_query_prompt,
    decide_action_prompt
)

class LLMClient:
    """
    A client for handling interactions with LLM providers (defaulting to OpenAI).
    
    This class manages the logic for transforming user requests through various 
    specialized prompts, including query refinement, search query generation, 
    codebase navigation, and agentic decision-making.
    """
    
    def __init__(self, provider: str = "openai"):
        self.provider = provider
        self.api_key = None
        self.client = None

        # ── Central model config ──────────────────────────────────────
        self.fast_model = "gpt-4o-mini"        # cheap/fast calls (query refinement, file targeting)
        self.reasoning_model = "gpt-4o"        # reasoning-heavy calls (search queries, action loop)
        # ──────────────────────────────────────────────────────────────

        if self.provider == "openai":
            self.api_key = os.getenv("OPENAI_API_KEY")
            if self.api_key:
                self.client = OpenAI(api_key=self.api_key)
            else:
                print("Warning: OPENAI_API_KEY not set. LLM calls will fail.")
        
    def refine_user_query(self, user_question: str, project_context: str = "", file_structure: str = "") -> Dict:
        """
        Refines a vague user query into a technical information need.
        Returns a dict with 'intent', 'refined_question', 'keywords', and 'is_action_request'.
        """
        if self.provider == "mock":
            return {
                "intent": "Search for code related to the question.",
                "refined_question": user_question,
                "keywords": user_question.split(),
                "is_action_request": False
            }

        prompt = refine_query_prompt(user_question, project_context, file_structure)

        if self.provider == "openai" and self.client:
            try:
                response = self.client.chat.completions.create(
                    model=self.fast_model,
                    messages=[{"role": "system", "content": "You are a helpful assistant. Return ONLY valid JSON."},
                              {"role": "user", "content": prompt}],
                    temperature=0.1
                )
                content = response.choices[0].message.content.strip()
                
                # Robust JSON extraction
                start_idx = content.find('{')
                end_idx = content.rfind('}')
                
                if start_idx != -1 and end_idx != -1:
                    json_str = content[start_idx:end_idx+1]
                    try:
                        data = json.loads(json_str)
                        # Ensure the key exists even if the LLM misses it
                        if "is_action_request" not in data:
                            data["is_action_request"] = False
                        if "target_files" not in data:
                            data["target_files"] = []
                        if "action_type" not in data:
                            data["action_type"] = None
                        if "direct_tool_call" not in data:
                            data["direct_tool_call"] = None
                        return data
                    except json.JSONDecodeError:
                        pass # Fall through to default
                        
            except Exception as e:
                print(f"[Query Expansion] Warning: Failed to refine query: {e}")
        
        return {
            "intent": "General code search",
            "refined_question": user_question,
            "keywords": [],
            "is_action_request": False
        }

    def identify_relevant_files(self, user_question: str, file_structure: str, symbol_minimap: Dict = None) -> List[str]:
        """
        Skeleton-first analysis: Given a project file tree and an optional symbol minimap,
        identify the files most likely to contain the answer.
        """
        if self.provider == "mock":
            return []

        minimap_hint = ""
        if symbol_minimap:
            # Create a condensed version of the minimap for the prompt
            parts = []
            for path, entry in list(symbol_minimap.items())[:50]: # Cap to 50 files for prompt space
                if isinstance(entry, list):
                    symbols = entry
                    file_keywords = []
                else:
                    symbols = entry.get("symbols", [])
                    file_keywords = entry.get("keywords", [])
                
                sym_strs = []
                for s in symbols[:10]: # Cap symbols per file
                    sig = s.get("signature", "")
                    doc = f" - {s['doc']}" if s.get("doc") else ""
                    sym_strs.append(f"{s['name']}{sig}{doc}")
                
                kw_str = f"  Keywords: {', '.join(file_keywords)}" if file_keywords else ""
                parts.append(f"### {path}\n" + "\n".join(f"  * {ss}" for ss in sym_strs) + (f"\n{kw_str}" if kw_str else ""))
            
            minimap_hint = f"\nSymbol MiniMap (Classes, Functions, Signatures, Keywords):\n" + "\n".join(parts) + "\n"

        prompt = identify_relevant_files_prompt(user_question, file_structure, minimap_hint)

        if self.provider == "openai" and self.client:
            try:
                response = self.client.chat.completions.create(
                    model=self.fast_model,
                    messages=[{"role": "system", "content": "You are a helpful assistant. Return ONLY valid JSON."},
                              {"role": "user", "content": prompt}],
                    temperature=0.1
                )
                content = response.choices[0].message.content.strip()
                
                # Robust JSON extraction
                match = re.search(r'\[.*\]', content, re.DOTALL)
                if match:
                    json_str = match.group(0)
                    try:
                        files = json.loads(json_str)
                        if isinstance(files, list):
                            print(f"[Skeleton] Identified {len(files)} relevant files: {files}")
                            return files[:8]
                    except json.JSONDecodeError as e:
                        print(f"[Skeleton] Warning: JSON Decode Error: {e}")
                else:
                    print(f"[Skeleton] Warning: No JSON list found in response: {content[:100]}...")
            except Exception as e:
                print(f"[Skeleton] Warning: Could not parse file list: {e}")
                return []

        return []

    def generate_search_queries(
        self,
        user_question: str,
        tool: str = "ripgrep",
        history: List[Dict] = None,
        project_context: str = "",
        file_structure: str = "",
        repo_name: str = "",
        language_hint: str = "",
    ) -> List[str]:
        if self.provider == "mock":
            words = user_question.split()
            return [w for w in words if len(w) > 3]

        # Format history for prompt
        history_str = ""
        if history:
            history_str = "Conversation History:\n" + "\n".join(
                [f"{msg['role'].upper()}: {msg['content']}" for msg in history]
            ) + "\n"

        structure_hint = ""
        if file_structure:
            structure_hint = f"""\nProject File Structure:
```
{file_structure[:2000]}
```
Use this structure to generate targeted queries. For example, if you see
'services/auth_service.py', search for function names or patterns likely
in that file.\n"""

        if tool == "github":
            prompt = github_search_query_prompt(
                user_question,
                repo_name=repo_name,
                language_hint=language_hint,
            )
        else:
            prompt = generate_search_queries_prompt(
                user_question, project_context, structure_hint, history_str
            )

        if self.provider == "openai" and self.client:
            try:
                response = self.client.chat.completions.create(
                    model=self.reasoning_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a code search specialist. "
                                       "Return only search queries, one per line.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                )
                content = response.choices[0].message.content
                content = content.replace("```", "").strip()
                queries = [line.strip() for line in content.splitlines() if line.strip()]

                if not queries:
                    print("[SearchQueries] Warning: LLM returned empty query list.")
                    return self._fallback_queries(user_question)

                print(f"[SearchQueries] Generated {len(queries)} queries.")
                return queries
    
            except Exception as e:
                print(f"[SearchQueries] Warning: API call failed: {e}")
                return self._fallback_queries(user_question)

    def _fallback_queries(self, user_question: str) -> List[str]:
        """
        Graceful degradation — extract meaningful words from the raw question.
        Filters out stop words and short tokens so ripgrep still gets
        something useful even when the LLM call fails entirely.
        """
        stop_words = {
            "how", "do", "i", "the", "a", "an", "is", "in", "to",
            "what", "does", "can", "for", "of", "and", "or", "my",
            "this", "that", "it", "with", "on", "at", "from", "be",
        }
        words = user_question.lower().split()
        return [w for w in words if len(w) > 3 and w not in stop_words][:5]

    def decide_action(
        self,
        user_question: str,
        context: str,
        project_structure: str = "",
        history: List[Dict] = None,
        available_tools: str = "",
        current_iteration: int = 1,
        max_iterations: int = 5,
    ) -> Dict:
        if self.provider == "mock":
            return {"action": "final_answer", "content": "[Mock Final Answer]"}

        history_str = ""
        if history:
            history_str = "Conversation History:\n"
            for msg in history:
                history_str += f"{msg['role'].capitalize()}: {msg['content']}\n"

        prompt = decide_action_prompt(
            user_question,
            context,
            history_str,
            project_structure,
            available_tools,
            current_iteration=current_iteration,
            max_iterations=max_iterations,
        )

        if self.provider == "openai" and self.client:
            try:
                response = self.client.chat.completions.create(
                    model=self.reasoning_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are an autonomous software agent. "
                                       "Return ONLY a valid JSON object — no markdown, no explanation.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                )
                content = response.choices[0].message.content.strip()

                # Strip markdown fences if model ignores instructions
                if content.startswith("```"):
                    lines = content.splitlines()
                    content = "\n".join(
                        lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
                    )

                start_idx = content.find("{")
                end_idx = content.rfind("}")
                if start_idx != -1 and end_idx != -1:
                    json_str = content[start_idx : end_idx + 1]
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError as e:
                        print(f"[Action Loop] JSON parse error: {e}. Raw: {content[:200]}")
                        # Degrade gracefully rather than silently swallowing
                        return {
                            "action": "final_answer",
                            "content": "I encountered a reasoning error. "
                                       "Here is what I found so far:\n\n" + context[:1000],
                            "thought": "JSON parse failed — returning partial context.",
                        }
                else:
                    # Model returned plain text — treat as final answer
                    return {"action": "final_answer", "content": content}

            except Exception as e:
                print(f"[Action Loop] Error: {e}")
                return {
                    "action": "final_answer",
                    "content": f"Agent encountered an error: {e}",
                    "thought": "Exception raised during API call.",
                }

        return {"action": "final_answer", "content": "LLM provider not configured."}

    def analyze_project_context(self, readme_content: str) -> str:
        if not readme_content or not readme_content.strip():
            return ""

        if self.provider == "mock":
            return "Mock Project Context"

        prompt = analyze_project_context_prompt(readme_content)

        if self.provider == "openai" and self.client:
            try:
                response = self.client.chat.completions.create(
                    model=self.fast_model,   # ← uses central config (Fix 5 prereq)
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a technical documentation analyst. "
                                       "Be concise and precise.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                print(f"[ProjectContext] Warning: {e}")
                return ""

        return ""

    def generate_questions(self, context: str, num: int = 5) -> str:
        """
        Generates sample questions and answers based on the project context.
        """
        if self.provider == "mock":
            return "1. **Question**: What is this? \n   - **Answer**: A mock project."

        # Truncate context to safe limit (approx 15k tokens) to avoid errors
        safe_context = context[:50000] 

        prompt = generate_questions_prompt(safe_context, num)

        if self.provider == "openai" and self.client:
            try:
                response = self.client.chat.completions.create(
                    model=self.reasoning_model,
                    messages=[{"role": "system", "content": "You are a helpful assistant."},
                              {"role": "user", "content": prompt}]
                )
                return response.choices[0].message.content
            except Exception as e:
                return f"Error generating questions: {e}"

        return "LLM Provider not configured."
