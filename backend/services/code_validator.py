"""Code Validator — AST-based Python code security analysis.

Checks code for dangerous operations before sandbox execution.
Returns (is_safe, reason) tuple.
"""

import ast
import logging
import re

logger = logging.getLogger(__name__)

# ── Blocked Modules ────────────────────────────────────────────────

BLOCKED_MODULES = {
    # System / Process
    'os', 'subprocess', 'sys', 'shutil', 'signal', 'multiprocessing',
    'threading', 'ctypes', 'resource',
    # Network
    'socket', 'http', 'http.client', 'http.server', 'ftplib', 'smtplib',
    'poplib', 'imaplib', 'urllib', 'urllib.request', 'urllib3',
    'requests', 'aiohttp', 'websocket',
    # Dangerous
    'importlib', 'code', 'codeop', 'compileall', 'zipimport',
    'pickle', 'shelve', 'marshal', 'dbm',
    # System info
    'platform', 'getpass', 'pwd', 'grp', 'crypt', 'termios', 'tty',
    'pty', 'fcntl', 'select', 'selectors', 'asyncio.subprocess',
}

# Allow these even if parent module is blocked
ALLOWED_SUBMODULES = {
    'http.cookies', 'http.cookiejar', 'urllib.parse',
}

# ── Blocked Built-in Functions ─────────────────────────────────────

BLOCKED_BUILTINS = {
    'exec', 'eval', 'compile', '__import__', 'globals', 'locals',
    'vars', 'dir', 'getattr', 'setattr', 'delattr',
    'breakpoint', 'exit', 'quit',
}

# ── Blocked Attribute Patterns ─────────────────────────────────────

BLOCKED_ATTR_PATTERNS = [
    '__subclasses__', '__bases__', '__globals__', '__code__',
    '__class__', '__mro__', '__subclasshook__',
    '__builtins__', '__import__', '__loader__',
    'system', 'popen', 'spawn', 'fork',
]

# ── Dangerous Function Calls ───────────────────────────────────────

BLOCKED_CALL_NAMES = {
    'exec', 'eval', 'compile', '__import__', 'breakpoint',
    'open',  # Will check mode in detail
}

# ── Allowed Modules (safe for data processing) ─────────────────────

ALLOWED_MODULES = {
    # Data processing
    'pandas', 'numpy', 'scipy', 'sklearn', 'scikit-learn',
    'matplotlib', 'seaborn', 'plotly',
    # Standard library (safe subset)
    'json', 'csv', 'math', 'statistics', 'random',
    'datetime', 'dateutil', 'time',
    'collections', 'itertools', 'functools', 'operator',
    're', 'string', 'textwrap', 'unicodedata',
    'copy', 'pprint', 'typing', 'dataclasses',
    'decimal', 'fractions', 'bisect', 'heapq', 'array',
    'enum', 'uuid', 'hashlib', 'base64', 'binascii',
    'io', 'tempfile', 'pathlib',
    'contextlib', 'abc', 'warnings',
}


class CodeValidator:
    """AST-based Python code security checker."""

    def __init__(self, extra_allowed_modules: set = None, extra_blocked_modules: set = None):
        self.blocked_modules = BLOCKED_MODULES.copy()
        self.allowed_modules = ALLOWED_MODULES.copy()
        if extra_blocked_modules:
            self.blocked_modules |= extra_blocked_modules
        if extra_allowed_modules:
            self.allowed_modules |= extra_allowed_modules

    def validate(self, code: str) -> tuple:
        """Validate code safety.

        Args:
            code: Python source code to validate.

        Returns:
            (is_safe: bool, reason: str)
            - (True, "") if code is safe
            - (False, "reason") if code is dangerous
        """
        # 1. Quick regex checks before AST parsing
        quick_check = self._quick_check(code)
        if quick_check:
            return False, quick_check

        # 2. AST parsing
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"语法错误: {e}"

        # 3. AST-based analysis
        for node in ast.walk(tree):
            # Check imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    ok, reason = self._check_module(alias.name)
                    if not ok:
                        return False, reason

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                ok, reason = self._check_module(module)
                if not ok:
                    return False, reason

            # Check function calls
            elif isinstance(node, ast.Call):
                ok, reason = self._check_call(node)
                if not ok:
                    return False, reason

            # Check attribute access
            elif isinstance(node, ast.Attribute):
                ok, reason = self._check_attribute(node)
                if not ok:
                    return False, reason

        return True, ""

    def _quick_check(self, code: str) -> str:
        """Quick regex-based checks before AST parsing."""
        # Check for __import__ string patterns
        if '__import__' in code and 'import' not in code.split('__import__')[0][-20:]:
            # __import__ used as string call, not in normal import context
            if re.search(r'__import__\s*\(', code):
                return "禁止使用 __import__()"

        # Check for os.system-like patterns in strings
        if re.search(r'(os|subprocess)\.(system|popen|call|run|check_output)\s*\(', code):
            return "禁止调用系统命令"

        # Check for exec/eval in strings
        if re.search(r'\b(exec|eval)\s*\(', code):
            return "禁止使用 exec/eval"

        return ""

    def _check_module(self, module_name: str) -> tuple:
        """Check if a module import is allowed."""
        # Check explicit allowlist first
        if module_name in self.allowed_modules:
            return True, ""

        # Check if it's a submodule of an allowed module
        top_level = module_name.split('.')[0]
        if top_level in self.allowed_modules:
            return True, ""

        # Check explicit blocklist
        if module_name in self.blocked_modules:
            return False, f"禁止导入模块: {module_name}"

        # Check if parent module is blocked
        if top_level in self.blocked_modules:
            # Check exception for allowed submodules
            if module_name in ALLOWED_SUBMODULES:
                return True, ""
            return False, f"禁止导入模块: {module_name}"

        # Unknown module — allow by default (will fail at runtime if not installed)
        # This is intentional: we want to allow any pip-installable library
        return True, ""

    def _check_call(self, node: ast.Call) -> tuple:
        """Check if a function call is dangerous."""
        # Check direct function calls like exec(), eval()
        if isinstance(node.func, ast.Name):
            if node.func.id in BLOCKED_BUILTINS:
                if node.func.id == 'open':
                    return self._check_open_call(node)
                return False, f"禁止调用内置函数: {node.func.id}()"

        # Check method calls like os.system()
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in BLOCKED_ATTR_PATTERNS:
                # Special case: allow open() as it's checked above
                if attr == 'open' and isinstance(node.func.value, ast.Name) and node.func.value.id == 'builtins':
                    return True, ""
                return False, f"禁止访问属性: .{attr}"

        return True, ""

    def _check_open_call(self, node: ast.Call) -> tuple:
        """Check open() calls — allow read, block write."""
        # open() with no mode arg defaults to 'r' (safe)
        if len(node.args) < 2:
            return True, ""

        # Check mode argument
        mode_arg = node.args[1] if len(node.args) > 1 else None
        if mode_arg is None:
            # Check keyword arg
            for kw in node.keywords:
                if kw.arg == 'mode':
                    mode_arg = kw.value
                    break

        if mode_arg and isinstance(mode_arg, ast.Constant):
            mode = str(mode_arg.value)
            if any(m in mode for m in ['w', 'a', 'x', '+']):
                return False, f"禁止以写入模式打开文件: open(..., '{mode}')"

        return True, ""

    def _check_attribute(self, node: ast.Attribute) -> tuple:
        """Check if attribute access is dangerous."""
        attr = node.attr
        if attr in BLOCKED_ATTR_PATTERNS:
            return False, f"禁止访问属性: .{attr}"
        return True, ""


# Singleton instance
code_validator = CodeValidator()
