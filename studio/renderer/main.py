import subprocess as _subprocess
import platform as _platform
import asyncio
import json
import sys
import time

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# ffmpeg-python (composer.py) spawns ffmpeg via subprocess.Popen; without this
# patch every ffmpeg run pops a visible console window.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **kw)

    _subprocess.Popen = _Popen
# ─────────────────────────────────────────────────────────────────────────────

from modules.brain import ContentBrain
from modules.asset_manager import AssetManager
from modules.audio import AudioEngine
from modules.composer import Composer
import os
import shutil

# Windows may default stdout to cp1252, which crashes on emoji output when
# output is piped/redirected. Force UTF-8 so the pipeline never dies mid-run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _with_retries(fn, *, attempts=4, backoff=8, what="operation"):
    """Retry a fallible call (e.g. transient Gemini 503 overloads)."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            if attempt >= attempts:
                raise
            print(f"⚠️ {what} failed (attempt {attempt}/{attempts}): {e}")
            print(f"   Retrying in {backoff}s...")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
    return None
def clean_cache():
    """
    Safely deletes temporary files.
    Includes a Safety Lock to prevent deleting anything outside the project.
    """
    print("🧹 Cleaning up temporary files...")
    
    # 1. Define the specific target folders
    folders_to_clean = [
        os.path.join(os.getcwd(), "assets", "audio_clips"),
        os.path.join(os.getcwd(), "assets", "video_clips"),
        os.path.join(os.getcwd(), "assets", "temp")
    ]

    for folder in folders_to_clean:
        # SAFETY CHECK 1: Ensure folder actually exists
        if not os.path.exists(folder):
            continue
            
        # SAFETY CHECK 2: Double check we are inside our project "assets" folder
        # This prevents the script from ever touching C:\ or System32
        if "assets" not in folder:
            print(f"   🚨 SECURITY ALERT: Skipping {folder} because it looks unsafe!")
            continue

        # Loop through files inside the folder
        for filename in os.listdir(folder):
            file_path = os.path.join(folder, filename)
            
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path) # Delete the file
                    print(f"      Deleted: {filename}") # Print so you can see it working
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path) # Delete subfolders if any
            except Exception as e:
                print(f"   ❌ Failed to delete {file_path}. Reason: {e}")
    
    print("✨ Workspace clean!")

async def main():
    print("🚀 STARTING AUTOMATION...")

    # CLI options:
    #   --topic "..."   use a specific topic instead of an AI-picked one
    #   --no-captions    disable burned-in captions
    topic = os.environ.get("TOPIC", "").strip()
    captions_enabled = "NO_CAPTIONS" not in os.environ
    for arg in sys.argv[1:]:
        if arg.startswith("--topic="):
            topic = arg.split("=", 1)[1].strip()
        elif arg.startswith("--topic "):
            topic = arg.split(" ", 1)[1].strip()
        elif arg == "--no-captions":
            captions_enabled = False

    # 1. BRAIN: Get Script
    brain = ContentBrain()
    try:
        if topic:
            print(f"🎯 Using provided topic: {topic}")
        else:
            topic = _with_retries(brain.get_trending_topic, what="topic generation")
        script = _with_retries(lambda: brain.generate_script(topic), what="script generation")
    except Exception as e:
        print(f"❌ Brain Error: {e}")
        return
    
    if not script:
        print("❌ Script generation failed.")
        return

    # Persist topic + script so the run is reproducible and inspectable.
    try:
        with open("topic.txt", "w", encoding="utf-8") as f:
            f.write(topic)
        with open("script.json", "w", encoding="utf-8") as f:
            json.dump({"topic": topic, "scenes": script}, f, indent=2, ensure_ascii=False)
        print(f"📝 Saved topic + script to topic.txt / script.json")
    except OSError as e:
        print(f"⚠️ Could not save topic/script: {e}")

    # 2. AUDIO: Generate Voice
    audio_engine = AudioEngine() 
    try:
        script = await audio_engine.process_script(script)
    except Exception as e:
        print(f"❌ Audio Error: {e}")
        return

    # 3. ASSETS: Get Stock Video
    asset_manager = AssetManager()
    assets_map = asset_manager.get_videos(script)

    # 4. COMPOSER: Merge Video + Audio
    composer = Composer(captions_enabled=captions_enabled)

    final_scene_paths = composer.render_all_scenes(script, assets_map)

    # 5. STITCH WITH TRANSITIONS
    if final_scene_paths:
        # CHANGED: Now using the transition function instead of simple concat
        composer.concatenate_with_transitions(final_scene_paths)
        clean_cache()
    else:
        print("❌ Failed to generate any scenes.")

if __name__ == "__main__":
    asyncio.run(main())