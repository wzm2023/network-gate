"""Check the actual Pages response, not just a successful upload status."""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

url = os.environ['SITE_URL'].rstrip('/') + '/data.json'
for attempt in range(4):
    try:
        with urllib.request.urlopen(url + '?verify=' + str(int(time.time())), timeout=15) as response:
            data = json.load(response)
        age = datetime.now(timezone.utc) - datetime.fromisoformat(data['generated_at'])
        if not 0 <= age.total_seconds() < 1200 or data['stats']['success'] < 3:
            raise RuntimeError('Published output is stale or below minimum node count')
        print(f"Published data verified: {data['stats']['success']} eligible nodes")
        break
    except Exception:
        if attempt == 3:
            raise
        time.sleep(10)
