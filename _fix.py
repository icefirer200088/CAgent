with open("agent.py") as f:
    lines = f.readlines()
# Line 333 (0-indexed 332) has CHARS_PER_TOKEN=*** which needs fixing
import re
old = lines[332]
new = re.sub(r'CHARS_PER_TOKEN=\*{3}\s+RECENT_TURNS_RESERVED\s*=',
             'CHARS_PER_TOKEN=***open("agent.py","w") as f:
    f.writelines(lines)
import py_compile
try:
    py_compile.compile("agent.py", doraise=True)
    print("Syntax OK")
except py_compile.PyCompileError as e:
    print(f"Still broken: {e}")
