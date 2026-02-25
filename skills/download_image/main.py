"""
Image Downloader - Robust "Honey Badger" Edition
Downloads images from direct URLs or extracts them from pages (Reddit, Pinterest, etc.).
Now ignores misleading Content-Type headers and sniffs actual file bytes.
"""

import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, unquote
import struct

# --- Configuration ---
# Pretend to be a real browser to avoid 403 Forbidden on some CDNs
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/"
}


MAGIC_NUMBERS = {
    'jpeg': (b'\xff\xd8\xff', 0),
    'png': (b'\x89PNG\r\n\x1a\n', 0),
    'gif': (b'GIF8', 0),
    'webp': (b'RIFF', 0), 
}

def get_image_type_from_bytes(data):
  
    if len(data) < 12: return None
    
    if data.startswith(MAGIC_NUMBERS['jpeg'][0]): return 'jpeg'
    if data.startswith(MAGIC_NUMBERS['png'][0]): return 'png'
    if data.startswith(MAGIC_NUMBERS['gif'][0]): return 'gif'
    
    # WEBP is a bit more complex: RIFF + length + WEBP
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'webp'
        
    return None

def download_image(url, filename):
    """
    Attempts to download an image from a URL.
    1. Tries direct download.
    2. Sniffs bytes to verify it's an image (ignoring Content-Type).
    3. If not an image, assumes it's a webpage and scrapes for og:image.
    """
    print(f"Target URL: {url}")
    
    try:
        # 1. Try Direct Download
        response = requests.get(url, headers=HEADERS, stream=True, timeout=15)
        response.raise_for_status()
        
        # Read the first chunk to sniff bytes
        first_chunk = next(response.iter_content(1024), b'')
        
        # 2. Analyze Bytes (The "Honey Badger" Check)
        detected_type = get_image_type_from_bytes(first_chunk)
        
        if detected_type:
            print(f"Bytes confirmed as image/{detected_type}. Downloading...")
            return save_stream(response, first_chunk, filename)
        else:
            print("Response is not a known image format. Treating as webpage...")
            # If it's not an image, it might be HTML. Let's read the rest and parse it.
            full_content = first_chunk + response.content
            return scrape_image_from_page(url, full_content, filename)

    except Exception as e:
        print(f"Error: {e}")
        return False

def save_stream(response, first_chunk, filename):
    """Saves the stream to a file, ensuring directory exists."""
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        
        with open(filename, 'wb') as f:
            f.write(first_chunk)
            for chunk in response.iter_content(1024):
                f.write(chunk)
                
        print(f"Successfully saved to {filename}")
        return True
    except Exception as e:
        print(f"Failed to save file: {e}")
        return False

def scrape_image_from_page(url, html_content, filename):
    """
    Parses HTML to find the highest resolution image (og:image, twitter:image, or direct links).
    """
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Candidates for the "best" image
        candidates = []
        
        # 1. Check Meta Tags (Highest Priority)
        meta_tags = [
            'og:image', 
            'twitter:image', 
            'twitter:image:src',
            'og:image:secure_url'
        ]
        
        for tag in meta_tags:
            meta = soup.find('meta', property=tag) or soup.find('meta', attrs={'name': tag})
            if meta and meta.get('content'):
                candidates.append(meta['content'])
                
        # 2. Look for large images in the body if no meta tags found
        if not candidates:
            imgs = soup.find_all('img')
            for img in imgs:
                src = img.get('src')
                if src and src.startswith('http'):
                    candidates.append(src)
        
        if not candidates:
            print("No suitable image found on page.")
            return False
            
        # 3. Resolve Relative URLs and Pick the First/Best
        best_image_url = candidates[0] # Usually og:image is best
        
        # Handle relative URLs (rare for og:image but possible for <img>)
        if not best_image_url.startswith('http'):
            from urllib.parse import urljoin
            best_image_url = urljoin(url, best_image_url)
            
        print(f"Found best image candidate: {best_image_url}")
        
        # Recursive call to download the extracted URL
        # We pass a flag or just rely on the byte sniffer to catch it this time
        return download_direct_candidate(best_image_url, filename)

    except Exception as e:
        print(f"Scraping failed: {e}")
        return False

def download_direct_candidate(url, filename):
    """
    Helper to download the scraped candidate URL. 
    Separate function to avoid infinite recursion loops.
    """
    try:
        response = requests.get(url, headers=HEADERS, stream=True, timeout=15)
        response.raise_for_status()
        
        # Sniff again just to be sure
        first_chunk = next(response.iter_content(1024), b'')
        detected_type = get_image_type_from_bytes(first_chunk)
        
        if detected_type:
            return save_stream(response, first_chunk, filename)
        else:
            print(f"Extracted URL {url} turned out not to be an image either.")
            return False
    except Exception as e:
        print(f"Failed to download candidate {url}: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python main.py <url> <filename>")
        sys.exit(1)

    target_url = sys.argv[1]
    output_filename = sys.argv[2]
    
    if download_image(target_url, output_filename):
        sys.exit(0)
    else:
        sys.exit(1)
