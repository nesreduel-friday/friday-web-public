import os
import requests
import random
from dotenv import load_dotenv

_STOPWORDS = {
    "close", "up", "down", "aerial", "drone", "macro", "underwater", "slow", "motion",
    "the", "a", "an", "and", "with", "of", "in", "on", "at", "top", "view", "from",
    "water", "ocean", "sea", "background", "4k", "hd", "footage", "video", "clip",
    "beautiful", "view", "camera", "shot", "light", "dark", "deep", "view",
}

class AssetManager:
    def __init__(self):
        load_dotenv()
        self.api_key = os.getenv("PEXELS_API_KEY")
        if not self.api_key:
            raise RuntimeError("PEXELS_API_KEY is not set. Create a .env file or set the environment variable before running.")
        self.base_url = "https://api.pexels.com/videos/search"
        self.headers = {
            "Authorization": self.api_key
        }
        self.pixabay_key = os.getenv("PIXABAY_API_KEY", "").strip()
        
        # Ensure download directory exists
        self.assets_dir = os.path.join(os.getcwd(), "assets", "video_clips")
        os.makedirs(self.assets_dir, exist_ok=True)

    def _simplify_query(self, query):
        """Strip stopwords so a retry keeps the real subject instead of 'up' or 'drone'."""
        words = [w for w in query.split() if w.lower() not in _STOPWORDS]
        return " ".join(words).strip() or None

    def search_video(self, query, duration_min=4):
        """
        Searches for a portrait video across all configured free sources:
        Pexels -> Pixabay (if key set).
        Returns the download URL or None.
        """
        # 1. Pexels (full query)
        url = self._search_pexels(query, duration_min)
        if url:
            return url
        # 2. Pexels with simplified query (stopwords dropped)
        simple_query = self._simplify_query(query)
        if simple_query and simple_query != query:
            print(f"   🔍 Retrying Pexels with simplified query: '{simple_query}'")
            url = self._search_pexels(simple_query, duration_min)
            if url:
                return url
        # 3. Pixabay (only if a key is configured)
        if self.pixabay_key:
            url = self._search_pixabay(query, duration_min)
            if url:
                return url
        return None

    def _search_pexels(self, query, duration_min=4):
        """Searches Pexels for a portrait video matching the query."""
        print(f"   🔍 Searching Pexels for: '{query}'...")
        
        params = {
            "query": query,
            "per_page": 5,        # Fetch top 5 results to pick from
            "orientation": "portrait",
            "size": "medium"      # 'medium' is usually HD ready, saves bandwidth
        }
        
        try:
            response = requests.get(self.base_url, headers=self.headers, params=params, timeout=10)
            if response.status_code != 200:
                print(f"      ⚠️ Pexels API Error: {response.status_code}")
                return None
                
            data = response.json()
            
            if not data.get('videos'):
                # Retry strategy: simplify query (drop stopwords) instead of taking a
                # random last word that can return unrelated footage.
                simple_query = self._simplify_query(query)
                if simple_query and simple_query != query:
                    print(f"      ⚠️ No results. Retrying with '{simple_query}'...")
                    return self._search_pexels(simple_query, duration_min)
                return None
            
            # Filter logic: Prefer videos that aren't too short (at least 4 seconds)
            valid_videos = [v for v in data['videos'] if v['duration'] >= duration_min]
            
            if not valid_videos:
                valid_videos = data['videos'] # Fallback to whatever exists
            
            # Pexels ranks by relevance: pick the FIRST match so we don't end up
            # with an unrelated animal (e.g. a penguin) from a random selection.
            selected_video = valid_videos[0]
            
            # Get best quality video file link
            video_files = selected_video['video_files']
            video_files.sort(key=lambda x: x['width'] * x['height'], reverse=True)
            
            download_link = video_files[0]['link']
            return download_link

        except Exception as e:
            print(f"      ❌ Error searching Pexels: {e}")
            return None

    def _search_pixabay(self, query, duration_min=4):
        """Searches Pixabay for a vertical video matching the query (key required)."""
        print(f"   🔍 Searching Pixabay for: '{query}'...")
        params = {
            "key": self.pixabay_key,
            "q": query,
            "per_page": 10,
            "safesearch": "true",
            "video_type": "film",
            "orientation": "vertical",
            "min_width": 720,
        }
        try:
            response = requests.get("https://pixabay.com/api/videos/", params=params, timeout=10)
            if response.status_code != 200:
                print(f"      ⚠️ Pixabay API Error: {response.status_code}")
                return None
            data = response.json()
            hits = data.get('hits', [])
            if not hits:
                simple_query = self._simplify_query(query)
                if simple_query and simple_query != query:
                    print(f"      ⚠️ No results. Retrying with '{simple_query}'...")
                    return self._search_pixabay(simple_query, duration_min)
                return None
            valid = [h for h in hits if h.get('duration', 0) >= duration_min]
            if not valid:
                valid = hits
            # Pick the highest-resolution format available.
            sizes = valid[0].get('videos', {})
            for name in ("large", "medium", "small"):
                if name in sizes and sizes[name].get('url'):
                    return sizes[name]['url']
            return None
        except Exception as e:
            print(f"      ❌ Error searching Pixabay: {e}")
            return None

    def download_video(self, url, filename):
        """
        Downloads the video content to a local file.
        """
        save_path = os.path.join(self.assets_dir, filename)
        
        # Caching strategy
        if os.path.exists(save_path):
            return save_path

        try:
            with requests.get(url, stream=True, timeout=15) as r:
                r.raise_for_status()
                with open(save_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            return save_path
        except Exception as e:
            print(f"      ❌ Error downloading {filename}: {e}")
            return None

    def get_video_by_id(self, video_id, filename):
        """Download a specific Pexels video by its ID (deterministic, no search)."""
        try:
            r = requests.get(f"https://api.pexels.com/videos/videos/{video_id}",
                             headers=self.headers, timeout=10)
            if r.status_code != 200:
                print(f"      ⚠️ Pexels video {video_id} failed: {r.status_code}")
                return None
            v = r.json()
            files = sorted(v.get('video_files', []),
                           key=lambda x: x.get('width', 0) * x.get('height', 0),
                           reverse=True)
            if not files:
                return None
            return self.download_video(files[0]['link'], filename)
        except Exception as e:
            print(f"      ❌ Error fetching video {video_id}: {e}")
            return None

    def get_videos(self, script_data):
        """
        NEW LOGIC: Downloads TWO videos per scene (A and B).
        Returns a list of tuples: [(path_a, path_b), (path_a, path_b), ...]
        """
        print("🎥 Starting Double-Feature Video Download...")
        video_pairs = []

        for scene in script_data:
            scene_id = scene['id']
            
            # 1. Get Search Terms
            # Fallback to 'keywords' if visual_1/2 don't exist (compatibility mode)
            query_a = scene.get('visual_1', scene.get('keywords', 'abstract'))
            query_b = scene.get('visual_2', query_a) # Use A if B is missing
            query_c = scene.get('visual_3', '')      # broader guaranteed-available shot
            pexels_a = scene.get('pexels_id_a')
            pexels_b = scene.get('pexels_id_b', pexels_a)
            
            # 2. Search & Download Clip A (pin to a fixed Pexels ID if provided)
            path_a = None
            if pexels_a:
                path_a = self.get_video_by_id(pexels_a, f"scene_{scene_id}_a.mp4")
            else:
                url_a = self.search_video(query_a)
                if url_a:
                    path_a = self.download_video(url_a, f"scene_{scene_id}_a.mp4")
            
            # 3. Search & Download Clip B (pin to a fixed Pexels ID if provided)
            path_b = None
            if pexels_b:
                path_b = self.get_video_by_id(pexels_b, f"scene_{scene_id}_b.mp4")
            else:
                url_b = self.search_video(query_b)
                if url_b:
                    path_b = self.download_video(url_b, f"scene_{scene_id}_b.mp4")
            
            # 3b. Broader fallback for A: visual_3 (guaranteed-available related shot)
            if not path_a and query_c:
                print(f"      ⚠️ Scene {scene_id} Clip A: no results for '{query_a}', trying broader '{query_c}'...")
                url_a = self.search_video(query_c)
                if url_a:
                    path_a = self.download_video(url_a, f"scene_{scene_id}_a.mp4")
            
            # 3c. Broader fallback for B: visual_3
            if not path_b and query_c:
                print(f"      ⚠️ Scene {scene_id} Clip B: no results for '{query_b}', trying broader '{query_c}'...")
                url_b = self.search_video(query_c)
                if url_b:
                    path_b = self.download_video(url_b, f"scene_{scene_id}_b.mp4")
            
            # 4. Fallback Logic (Self-Healing)
            # If B fails, use A twice. If A fails, use B twice.
            if not path_a and path_b: 
                path_a = path_b
                print(f"      ⚠️ Scene {scene_id} Clip A missing. Using Clip B for both.")
            if not path_b and path_a: 
                path_b = path_a
                print(f"      ⚠️ Scene {scene_id} Clip B missing. Using Clip A for both.")

            # 5. Final Check
            if path_a and path_b:
                video_pairs.append((path_a, path_b))
                print(f"   ✅ Scene {scene_id} Ready (A + B).")
            else:
                print(f"   ❌ Scene {scene_id} Completely Failed (No videos found).")
                video_pairs.append(None)

        return video_pairs

# --- TESTING ---
if __name__ == "__main__":
    manager = AssetManager()
    
    # Test with new dual-visual format
    test_script = [
        {
            "id": 1, 
            "visual_1": "cyberpunk city neon", 
            "visual_2": "hacker typing computer"
        }
    ]
    
    results = manager.get_videos(test_script)
    print("🎥 Assets Downloaded:", results)