import json
from pathlib import Path

# Pegar último arquivo JSON
json_file = sorted(Path("debug").glob("ai_response_batch*.json"), reverse=True)[0]
print(f"Arquivo: {json_file.name}\n")

with open(json_file) as f:
    data = json.load(f)

# Ver keys disponíveis
if isinstance(data, dict) and 'resultados' in data:
    post = data['resultados'][0] if data['resultados'] else {}
else:
    post = data

print("Keys disponíveis no JSON:")
for key in post.keys():
    value = post[key]
    if isinstance(value, str):
        print(f"  - {key}: {value[:100] if len(value) > 100 else value}")
    else:
        print(f"  - {key}: {type(value).__name__}")
