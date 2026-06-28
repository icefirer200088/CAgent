import re

with open("/root/CAgent/agent.py") as f:
    content = f.read()

# Replace CHARS_PER_TOKEN=*** (where *** = three asterisks) with CHARS_PER_TOKEN=*** three_asterisks = chr(42) * 3
pattern = f"CHARS_PER_TOKEN={three_asterisks}    RECENT_TURNS_RESERVED"
replacement = "CHARS_PER_TOKEN=*** open("/root/CAgent/agent.py", "w") as f:
    f.write(content)

import py_compile
py_compile.compile("/root/CAgent/agent.py", doraise=True)
print("OK")
