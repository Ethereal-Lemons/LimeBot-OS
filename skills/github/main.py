import sys
import json
import urllib.request
from urllib.error import URLError, HTTPError
import os

def get_token():
    # Attempt to load token from .env file
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
    try:
        with open(env_path, 'r') as f:
            for line in f:
                if line.startswith('GITHUB_TOKEN=') or line.startswith('GH_TOKEN='):
                    return line.strip().split('=', 1)[1]
    except Exception:
        pass
    # Fallback to environment variables
    return os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')

def api_request(method, endpoint, data=None):
    token = get_token()
    if not token:
        print("Error: GitHub token not found in .env")
        sys.exit(1)
    
    url = f"https://api.github.com{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "LimeBot-GitHub-Skill"
    }
    
    req_data = json.dumps(data).encode('utf-8') if data else None
    req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 204:
                return None
            return json.loads(response.read().decode())
    except HTTPError as e:
        print(f"HTTP Error {e.code}: {e.read().decode()}")
        sys.exit(1)
    except URLError as e:
        print(f"URL Error: {e.reason}")
        sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <command> [args...]")
        print("Commands:")
        print("  list-repos")
        print("  accept-invites")
        print("  create-pr <owner/repo> <head_branch> <base_branch> <title> [body]")
        print("  user-info")
        sys.exit(1)
        
    cmd = sys.argv[1]
    
    if cmd == "list-repos":
        repos = api_request("GET", "/user/repos?per_page=100")
        for r in repos:
            print(f"{r['full_name']} - {r['html_url']}")
            
    elif cmd == "accept-invites":
        invites = api_request("GET", "/user/repository_invitations")
        if not invites:
            print("No pending repository invitations.")
            return
        for inv in invites:
            inv_id = inv['id']
            repo_name = inv['repository']['full_name']
            print(f"Accepting invitation for {repo_name} (ID: {inv_id})...")
            api_request("PATCH", f"/user/repository_invitations/{inv_id}")
            print("Accepted.")
            
    elif cmd == "create-pr":
        if len(sys.argv) < 6:
            print("Usage: create-pr <owner/repo> <head_branch> <base_branch> <title> [body]")
            sys.exit(1)
        repo = sys.argv[2]
        head = sys.argv[3]
        base = sys.argv[4]
        title = sys.argv[5]
        body = sys.argv[6] if len(sys.argv) > 6 else ""
        
        data = {
            "title": title,
            "body": body,
            "head": head,
            "base": base
        }
        res = api_request("POST", f"/repos/{repo}/pulls", data)
        print(f"Pull Request created successfully: {res.get('html_url')}")
        
    elif cmd == "user-info":
        user = api_request("GET", "/user")
        print(f"Authenticated as: {user.get('login')} ({user.get('name')})")
        print(f"Public Repos: {user.get('public_repos')}")
        print(f"Followers: {user.get('followers')}")
        
    else:
        print(f"Unknown command: {cmd}")

if __name__ == '__main__':
    main()
