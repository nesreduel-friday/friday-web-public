import os
import json
import time
from google import genai
from dotenv import load_dotenv

load_dotenv()

_GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-flash-lite-latest",
]

def _get_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Create a .env file or set the environment variable before running.")
    return genai.Client(api_key=api_key)

def _generate(client, prompt):
    """Try several models with patience; the free-tier key often 429/503s."""
    configured = os.getenv("GEMINI_MODEL")
    models = [configured] if configured else list(_GEMINI_MODELS)
    last_error = None
    for model in models:
        for attempt in range(4):
            try:
                response = client.models.generate_content(model=model, contents=prompt)
                text = response.text.strip()
                if text:
                    return text
                last_error = ValueError("Gemini returned an empty response.")
            except Exception as e:
                last_error = e
                msg = str(e)
                if any(k in msg for k in ("429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE")):
                    wait = 4 * (attempt + 1)
                    print(f"⚠️ Gemini {model} overloaded (attempt {attempt + 1}/4) — retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                raise e
        print(f"⚠️ Gemini {model} exhausted — switching model...")
    raise last_error or RuntimeError("Gemini request failed.")

class ContentBrain:
    def get_trending_topic(self):
        """
        In a full build, this would scrape Google Trends or Twitter.
        For now, we ask Gemini to pick a viral niche topic.
        """
        prompts = "Give me 1 specific, viral, and engaging topic for a Short Documentary. It should be a 'Engaging Did you know' fact or a 'Fun/intriguing Engaging News'. return ONLY the topic name."
        client = _get_client()
        topic = _generate(client, prompts)
        print(f"🎯 Selected Topic: {topic}")
        return topic

    def generate_script(self, topic):
        """
        Generates a structured JSON script with visual cues.
        """
        print(f"📝 Writing script for: {topic}...")
        prompt = f"""
You are the lead scriptwriter for a high-retention "Edutainment" YouTube Shorts channel.
Topic: {topic}

### GOAL:
Create a script where every sentence has a "Visual Switch". 
To keep retention high, we need TWO different stock videos for every single scene.

### 1. SCRIPT REQUIREMENTS (The Voiceover):
- **Perspective:** Strictly **3rd Person** ("Scientists found...", "The ocean hides...").
- **Tone:** Engaging, fast-paced, logical. No fluff.
- **Structure:** EXACTLY 7 scenes. Total spoken length 28-36 seconds.
  This is not negotiable: YouTube only pushes a Short wider when most viewers
  finish it, and every extra sentence costs completions.
- **Flow:** Hook -> Stakes -> Mechanism (How it works) -> Twist -> Loop.

#### RETENTION RULES (these decide whether the video gets distribution at all):
- **Scene 1 is the hook and must land in under 2 seconds. Maximum 12 words.**
  A concrete, surprising, specific claim that opens a question in the viewer's
  head. NEVER open with a greeting, a definition, "Have you ever wondered",
  "Did you know", "Imagine", or any other throat-clearing. Start mid-action.
- **Every scene delivers ONE genuinely new piece of information.** If a
  sentence only rephrases or sets up the one before it, merge them.
- **No scene may be a summary.** Summaries are exactly where viewers swipe away.
- **Scene 7 must close the loop:** answer the hook reusing two or three of the
  hook's own distinctive words, so the ending flows straight back into scene 1
  on replay. Replays are the strongest ranking signal a Short can earn, and a
  seamless loop is how you get them. Do NOT end with a moral, a "so next time
  you see..." line, or a spoken call to action -- the subscribe prompt is
  burned in visually and must not cost narration seconds.
- Keep every sentence under 16 words.

### 2. VISUAL REQUIREMENTS (Dual Visuals):
- For EVERY scene, provide TWO distinct search terms:
  - **visual_1:** Matches the *start* of the sentence.
  - **visual_2:** Matches the *end* of the sentence or provides a reaction/context.
- **MAIN SUBJECT MUST APPEAR:** The topic's main subject (e.g. the animal) MUST
  be the dominant word in the visual search terms of the scenes that describe it.
  E.g. for a video about tardigrades, most scenes must search "tardigrade ..." or
  "microscopic organism ...", NOT generic "laboratory" or "nature".
- **STOCK-FRIENDLY TERMS:** Search terms must be short, broad, common stock
  phrases (2-4 words) that Pexels/Pixabay actually have footage for. Prefer
  plain nouns + a scene word: "tardigrade close up", "microscope organism",
  "arctic fox snow", "honey bee flower". Avoid ultra-specific or rare phrasings
  that have no stock results.
- **Strictly Literal:** If the text is "The economy crashed," do NOT search "sad man". Search "Stock market red chart".
- If a subject is nearly impossible to film (e.g. a microscopic organism),
  ALSO provide a "visual_3" fallback: a broader, guaranteed-available related
  shot (e.g. "microscope laboratory", "tardigrade microscope") for that scene.

### OUTPUT FORMAT (Strict JSON):
[
    {{
        "id": 1,
        "text": "Fourteen wolves were released in Yellowstone, and the rivers moved.",
        "visual_1": "wolves running snow aerial",
        "visual_2": "river flowing forest drone",
        "visual_3": "wolves forest",
        "mood": "intriguing" 
    }},
    {{
        "id": 2,
        "text": "It sounds impossible, but the biology is actually simple math.",
        "visual_1": "person shocked looking at camera",
        "visual_2": "blackboard math equations chalk",
        "visual_3": "science laboratory",
        "mood": "educational"
    }}
]
"""

    #     prompt = f"""
    # You are a master visual storyteller creating a viral YouTube Short.
    # Topic: {topic}
    
    # ### CRITICAL REQUIREMENTS:
    # 1. **Perspective:** Strictly **3rd Person** (e.g., "Scientists discovered..." or "The world changed..."). No "I" or "You".
    # 2. **Tone:** Cinematic, high-stakes, and slightly exaggerated.
    #    - Use **Power Words**: Instead of "big," use "colossal." Instead of "scary," use "terrifying."
    #    - The vibe should be "Mystery Documentary" (like Vox or National Geographic but faster).
    # 3. **Length:** Exactly **8 to 9 scenes**. Total read time 40-50 seconds.
    # 4. **Visual Strategy:** Keywords must be optimized for Pexels Stock Footage.
    #    - Use simple, broad nouns: "storm clouds", "ancient ruins", "laboratory microscope".
    #    - Avoid complex actions or specific people.
    
    # ### STRUCTURE GUIDE:
    # - **Scene 1 (The Hook):** A mind-blowing statement or paradox. Grab attention immediately.
    # - **Scene 2-3 (The Mystery):** Establish why this is strange, dangerous, or important.
    # - **Scene 4-7 (The Climax):** The "Wait, what?" moment. The biggest twist or fact.
    # - **Scene 8-9 (The Mic Drop):** A final haunting thought or powerful conclusion.
    
    # ### OUTPUT FORMAT (Strict JSON):
    # [
    #     {{
    #         "id": 1,
    #         "text": "Deep beneath the Antarctic ice, something IMPOSSIBLE has just been detected.",
    #         "keywords": "glacier aerial drone cinematic",
    #         "mood": "ominous" 
    #     }},
    #     {{
    #         "id": 2,
    #         "text": "For centuries, maps showed this area as empty... they were wrong.",
    #         "keywords": "old map ancient paper table",
    #         "mood": "mystery"
    #     }}
    # ]
    # """
    

        client = _get_client()
        raw = _generate(client, prompt)
        
        # Clean the response to ensure it's valid JSON (sometimes AI adds markdown)
        clean_text = raw.replace('```json', '').replace('```', '').strip()
        
        try:
            script_data = json.loads(clean_text)
            self._pin_holding_scene(script_data, topic)
            return script_data
        except json.JSONDecodeError:
            print("❌ Error parsing JSON. Raw output:")
            print(clean_text)
            return None

    def _pin_holding_scene(self, script_data, topic=""):
        """
        Pexels has almost no footage of otters holding paws, so free-text search
        tends to return unrelated clips. When a scene is literally about paw/arm
        holding, force the one known clip (34213432) that clearly shows two sea
        otters holding paws while floating on their backs.

        This only applies to sea-otter videos.  The trigger words below are far
        too common to use on their own -- "lock", "hold", "grip" and "hand" show
        up in almost any script ("once the target is locked", "a grip on the
        branch"), and without the topic check an owl or octopus video would
        silently get sea-otter footage spliced into it.
        """
        if "otter" not in str(topic).lower():
            return
        scenes = script_data if isinstance(script_data, list) else script_data.get("scenes", [])
        pinned = False
        for scene in scenes:
            text = str(scene.get("text", "")).lower()
            if any(k in text for k in ("paw", "hand", "lock", "grip", "cling", "hold")):
                scene["pexels_id_a"] = 34213432
                scene["pexels_id_b"] = 34213432
                print(f"🎯 Scene {scene.get('id')}: pinned paw-holding clip 34213432")
                pinned = True
        if pinned:
            # Neighbouring scene ("acts like an anchor") benefits from the same shot.
            for i, scene in enumerate(scenes):
                if scene.get("pexels_id_a"):
                    if i + 1 < len(scenes):
                        scenes[i + 1]["pexels_id_b"] = 34213432
                    break
        
# --- TESTING THE MODULE ---
if __name__ == "__main__":
    brain = ContentBrain()
    topic = brain.get_trending_topic()
    script = brain.generate_script(topic)
    
    # Save to file to verify
    with open("script.json", "w") as f:
        json.dump(script, f, indent=4)
        print("✅ Script saved to script.json")