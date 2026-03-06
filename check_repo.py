import urllib.request
url = 'https://raw.githubusercontent.com/Wan-Video/Wan2.1/main/README.md'
try:
    with urllib.request.urlopen(url) as response:
        content = response.read().decode('utf-8')
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'FLF2V' in line:
                 print(f"Line {i}: {line.strip().encode('ascii', 'ignore').decode('ascii')}")
except Exception as e:
    print(f"Error: {e}")
