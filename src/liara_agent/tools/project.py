"""Read-only tools the LLM uses to investigate a project. Sandboxed to the project root; secrets never leave."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from liara_agent.detect.fingerprints import SKIP_DIRS, fingerprint
from liara_agent.liara import docs
from liara_agent.plan.model import DeployPlan

MAX_OUT = 6000
SECRET_FILE = re.compile(r"(^|/)(\.env(\..+)?|.*\.(pem|key|p12|pfx)|id_rsa.*|credentials.*\.json)$", re.I)
SAFE_ENV_FILE = re.compile(r"\.env\.(example|sample|template|dist)$", re.I)
SECRET_ASSIGN = re.compile(r"""(?ix)((?:secret|password|passwd|token|api[_-]?key|private[_-]?key|auth)\w*\s*[=:]\s*['"]?)([A-Za-z0-9_\-./+=]{12,})""")


class ToolError(Exception):
    pass


class ProjectTools:
    def __init__(self, root: str | Path, ask: Callable[[str], str] | None = None):
        self.root = Path(root).resolve()
        self.ask = ask
        self.submitted: DeployPlan | None = None
        self._warned_parts = False

    # -- helpers ------------------------------------------------------------------------------------------------
    def _resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise ToolError(f"path {rel!r} is outside the project")
        return p

    @staticmethod
    def _cap(text: str) -> str:
        return text if len(text) <= MAX_OUT else text[:MAX_OUT] + f"\n... [truncated, {len(text) - MAX_OUT} more characters]"

    # -- tools ----------------------------------------------------------------------------------------------------
    def list_dir(self, path: str = ".", depth: int = 2) -> str:
        base = self._resolve(path)
        if not base.is_dir():
            raise ToolError(f"{path!r} is not a directory")
        lines: list[str] = []

        def walk(d: Path, level: int) -> None:
            for p in sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name)):
                if p.name in SKIP_DIRS or p.name.startswith(".git"):
                    continue
                rel = p.relative_to(self.root).as_posix()
                lines.append(rel + ("/" if p.is_dir() else ""))
                if p.is_dir() and level < min(depth, 4):
                    walk(p, level + 1)

        walk(base, 1)
        return self._cap("\n".join(lines) or "(empty)")

    def read_file(self, path: str, start_line: int = 1, max_lines: int = 200) -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise ToolError(f"{path!r} is not a file")
        rel = p.relative_to(self.root).as_posix()
        if SECRET_FILE.search(rel) and not SAFE_ENV_FILE.search(rel):
            keys = [ln.split("=", 1)[0].removeprefix("export ").strip() for ln in p.read_text(errors="ignore").splitlines()
                    if "=" in ln and not ln.lstrip().startswith("#")]
            return f"[{rel} holds secrets; values are hidden from you on purpose]\nvariable names: {', '.join(keys) or '(none or not a key=value file)'}"
        if p.stat().st_size > 400_000:
            raise ToolError(f"{path!r} is too large to read; use grep")
        lines = p.read_text(errors="ignore").splitlines()
        chunk = lines[max(start_line - 1, 0): max(start_line - 1, 0) + max_lines]
        text = "\n".join(f"{i}: {SECRET_ASSIGN.sub(r'\1<redacted>', ln)}" for i, ln in enumerate(chunk, max(start_line, 1)))
        more = f"\n[{len(lines)} lines total]" if len(lines) > start_line - 1 + max_lines else ""
        return self._cap(text + more)

    def grep(self, pattern: str, path: str = ".", max_results: int = 40) -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"invalid regex: {e}")
        base = self._resolve(path)
        files = [base] if base.is_file() else sorted(f for f in base.rglob("*") if f.is_file())
        hits: list[str] = []
        for f in files:
            rel = f.relative_to(self.root).as_posix()
            if any(part in SKIP_DIRS for part in f.relative_to(self.root).parts) or SECRET_FILE.search(rel) and not SAFE_ENV_FILE.search(rel):
                continue
            if f.stat().st_size > 400_000:
                continue
            try:
                for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{rel}:{i}: {SECRET_ASSIGN.sub(r'\1<redacted>', line.strip())[:200]}")
                        if len(hits) >= max_results:
                            return self._cap("\n".join(hits) + "\n[max_results reached]")
            except OSError:
                continue
        return self._cap("\n".join(hits) or "no matches")

    def search_docs(self, query: str, max_results: int = 3) -> str:
        """Liara's own documentation (a snapshot): how a platform, database or service is meant to be configured."""
        return self._cap(docs.as_text(docs.search(query, max(1, min(int(max_results), 5)))))

    def ask_user(self, question: str, options: list[str] | None = None) -> str:
        if not self.ask:
            return "The user cannot be asked right now. Choose the most conservative default and record the assumption as a note."
        return self.ask(question, options) if options else self.ask(question)

    def _uncovered_parts(self, plan: DeployPlan) -> list[tuple[str, str]]:
        """Sub-folders whose own manifest says web app / service (react, next, node server, django...) that no app of the plan covers: the usual miss in a monorepo."""
        deployable = {"node", "next", "react", "vue", "angular", "django", "flask", "php", "laravel", "go", "dotnet", "docker"}
        apps = [a.path.strip("./") for a in plan.apps]
        if "" in apps:
            return []
        out: dict[str, str] = {}
        for h in fingerprint(self.root).hints:
            d = (h.path or ".").strip("./")
            if h.kind == "platform" and h.value in deployable and d and not any(d == a or d.startswith(a + "/") or a.startswith(d + "/") for a in apps):
                out.setdefault(d, h.value)
        return sorted(out.items())

    def submit_plan(self, **plan) -> str:
        parsed = DeployPlan.model_validate(plan)  # pydantic errors are returned to the model so it can fix them
        problems = []
        for a in parsed.apps:
            if not a.evidence:
                problems.append(f"app {a.name!r} has no evidence (file and line); cite where you found the platform and start command")
            if a.role == "web" and not a.port:
                problems.append(f"app {a.name!r} is a web app but has no port")
        for d in parsed.databases:
            if not d.evidence:
                problems.append(f"database {d.kind!r} has no evidence; cite the dependency or config that needs it")
        if not parsed.apps:
            problems.append("a plan needs at least one app")
        if problems:
            raise ToolError("plan rejected: " + "; ".join(problems))
        left = self._uncovered_parts(parsed)
        if left and not self._warned_parts:  # once: a folder that is not a deployable part (docs, scripts, a shared library) is then accepted as it is
            self._warned_parts = True
            raise ToolError("plan not accepted yet: " + "; ".join(f"folder {d!r} looks like a deployable {p} part but no app covers it" for d, p in left)
                            + ". Add an AppSpec for each (path = that folder), or submit again unchanged if it is not part of the deployment.")
        self.submitted = parsed
        return "plan accepted"

    # -- dispatch -----------------------------------------------------------------------------------------------
    def call(self, name: str, arguments: str | dict) -> str:
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
            fn = {"list_dir": self.list_dir, "read_file": self.read_file, "grep": self.grep, "search_docs": self.search_docs,
                  "ask_user": self.ask_user, "submit_plan": self.submit_plan}.get(name)
            if not fn:
                raise ToolError(f"unknown tool {name!r}")
            return fn(**(args or {}))
        except ToolError as e:
            return f"error: {e}"
        except TypeError as e:
            return f"error: bad arguments: {e}"
        except json.JSONDecodeError as e:
            return f"error: arguments were not valid JSON: {e}"
        except Exception as e:  # pydantic ValidationError etc.: show the model what to fix
            return f"error: {type(e).__name__}: {str(e)[:1500]}"


def tool_schemas() -> list[dict]:
    def fn(name, description, props, required=()):
        return {"type": "function", "function": {"name": name, "description": description,
                "parameters": {"type": "object", "properties": props, "required": list(required)}}}

    plan_schema = DeployPlan.model_json_schema()
    return [
        fn("list_dir", "List files and folders (skips node_modules, .git, build output).",
           {"path": {"type": "string"}, "depth": {"type": "integer"}}),
        fn("read_file", "Read a text file with line numbers. Secret files show variable names only.",
           {"path": {"type": "string"}, "start_line": {"type": "integer"}, "max_lines": {"type": "integer"}}, ["path"]),
        fn("grep", "Regex search across the project; returns file:line: text.",
           {"pattern": {"type": "string"}, "path": {"type": "string"}, "max_results": {"type": "integer"}}, ["pattern"]),
        fn("search_docs", "Search Liara's documentation for how a platform, database or service is configured (ports, env variables, liara.json, limits). Use it when unsure about a Liara fact instead of guessing.",
           {"query": {"type": "string"}, "max_results": {"type": "integer"}}, ["query"]),
        fn("ask_user", "Ask the developer a short question when the code cannot answer it. Give `options` (short answers) when the choices are known.",
           {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, ["question"]),
        {"type": "function", "function": {"name": "submit_plan", "description": "Submit the final deployment plan. Ends the investigation.",
                                          "parameters": plan_schema}},
    ]
