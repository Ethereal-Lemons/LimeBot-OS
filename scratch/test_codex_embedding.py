import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.oauth_profiles import resolve_codex_oauth_api_key
from litellm import embedding

async def main():
    try:
        api_key = resolve_codex_oauth_api_key()
        print("Successfully resolved Codex OAuth API Key.")
        print(f"Key starts with: {api_key[:15]}...")
        
        # Test standard litellm embedding with this key
        print("Attempting to get embedding using standard OpenAI endpoint...")
        response = embedding(
            model="text-embedding-3-small",
            input=["Hello world"],
            api_key=api_key
        )
        print("Success! Embedding length:", len(response.data[0]["embedding"]))
    except Exception as e:
        print("Error encountered:", e)

if __name__ == "__main__":
    asyncio.run(main())
