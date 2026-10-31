import os
import random
import ffmpeg

class Composer:
    def __init__(self, captions_enabled=True, font_size=64, font_path=None):
        self.temp_dir = os.path.join(os.getcwd(), "assets", "temp")
        self.final_dir = os.path.join(os.getcwd(), "assets", "final")

        os.makedirs(self.temp_dir, exist_ok=True)
        os.makedirs(self.final_dir, exist_ok=True)
        self.transitions = ['fade', 'diagbr', 'diagtl']

        # Viral-Shorts caption style: big bold white text with a black outline.
        self.captions_enabled = captions_enabled
        self.font_size = font_size
        if font_path is None:
            font_path = self._pick_bold_font()
        self.font_path = font_path

    @staticmethod
    def _pick_bold_font():
        """Return an installed bold font path or None (drawtext then uses default)."""
        # Prefer a font copied into the project: relative paths avoid the
        # Windows drive-colon escaping problem inside ffmpeg filter graphs.
        local = os.path.join("assets", "fonts", "impact.ttf")
        if os.path.exists(local):
            return local
        for candidate in (
            "C:/Windows/Fonts/impact.ttf",      # classic meme/shorts font
            "C:/Windows/Fonts/arialbd.ttf",     # Arial Bold
            "C:/Windows/Fonts/bahnschrift.ttf",
            "C:/Windows/Fonts/seguibl.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
        ):
            if os.path.exists(candidate):
                return candidate
        return None

    @staticmethod
    def _wrap_text(text, max_chars=28):
        """Break a scene's narration into short caption lines (bold-shorts style)."""
        words = text.split()
        lines = []
        current = ""
        for word in words:
            if current and len(current) + 1 + len(word) > max_chars:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            lines.append(current)
        return lines

    @staticmethod
    def _escape_drawtext(text):
        """Escape characters that ffmpeg drawtext treats specially."""
        return (text.replace('\\', '\\\\')
                    .replace(':', '\\:')
                    .replace("'", "\\'")
                    .replace('%', '\\%'))

    def _caption_filter(self, video_stream, text):
        """Return a drawtext filter that burns bold captions into the frame."""
        lines = self._wrap_text(text)
        caption_text = "\n".join(lines)

        fontfile = self.font_path.replace('\\', '/') if self.font_path else None
        args = {
            "text": self._escape_drawtext(caption_text),
            "fontcolor": "white",
            "fontsize": str(self.font_size),
            "borderw": "6",
            "bordercolor": "black",
            "x": "(w-text_w)/2",
            "y": "(h-text_h)/2",
            "line_spacing": "14",
            "text_align": "center",
        }
        if fontfile:
            args["fontfile"] = fontfile
        return video_stream.filter("drawtext", **args)

    def _cta_filter(self, video_stream, total_duration):
        """Burn a subscribe prompt over the last few seconds of the video.

        A visual CTA is the one that actually converts: a large share of Shorts
        viewers watch on mute, so a spoken "subscribe" reaches none of them --
        and unlike a spoken CTA this costs zero narration seconds, so it never
        eats into retention.  It sits below the caption block so the two do not
        overlap.  Set CTA_TEXT="" to disable.
        """
        seconds = float(os.getenv("CTA_SECONDS", "3.2"))
        text = os.getenv("CTA_TEXT", "SUBSCRIBE\nfor a new one daily")
        if not text.strip() or total_duration <= seconds:
            return video_stream

        start = max(0.0, total_duration - seconds)
        args = {
            "text": self._escape_drawtext(text),
            "fontcolor": "white",
            "fontsize": str(int(self.font_size * 0.95)),
            "borderw": "6",
            "bordercolor": "black",
            "x": "(w-text_w)/2",
            "y": "h*0.76",
            "line_spacing": "10",
            "text_align": "center",
            "box": "1",
            "boxcolor": "black@0.45",
            "boxborderw": "24",
            "enable": f"between(t,{start:.2f},{total_duration:.2f})",
        }
        if self.font_path:
            args["fontfile"] = self.font_path.replace('\\', '/')
        print(f"   📣 Subscribe CTA burned in at {start:.1f}s-{total_duration:.1f}s")
        return video_stream.filter("drawtext", **args)

    def get_duration(self, filepath):
        try:
            probe = ffmpeg.probe(filepath)
            return float(probe['format']['duration'])
        except:
            return 0.0

    def process_scene(self, scene, video_pair):
        """
        Combines Audio with Visuals.
        Splits duration 50/50 between Video A and Video B.
        """
        scene_id = scene['id']
        audio_path = scene['audio_path']
        total_duration = scene['duration']
        output_path = os.path.join(self.temp_dir, f"scene_{scene_id}.mp4")

        try:
            input_audio = ffmpeg.input(audio_path)

            # --- DUAL VIDEO MODE (50/50 Split) ---
            print(f"   ⚙️ Processing Scene {scene_id}: 🎞️ A/B Split Mode")
            path_a, path_b = video_pair
            
            duration_a = total_duration / 2
            duration_b = (total_duration / 2) + 0.5 

            stream_a = (
                ffmpeg.input(path_a, stream_loop=-1)
                .trim(duration=duration_a)
                .setpts('PTS-STARTPTS')
                .filter('scale', 1080, 1920).filter('crop', 1080, 1920)
                .filter('fps', fps=30, round='up')
            )

            stream_b = (
                ffmpeg.input(path_b, stream_loop=-1)
                .trim(duration=duration_b)
                .setpts('PTS-STARTPTS')
                .filter('scale', 1080, 1920).filter('crop', 1080, 1920)
                .filter('fps', fps=30, round='up')
            )

            video_stream = ffmpeg.concat(stream_a, stream_b, v=1, a=0)

            # Burn bold-shorts captions directly into the frame.
            if self.captions_enabled and scene.get('text'):
                video_stream = self._caption_filter(video_stream, scene['text'])

            # Combine Video + Audio
            runner = ffmpeg.output(
                video_stream, 
                input_audio, 
                output_path, 
                vcodec='libx264', 
                acodec='aac', 
                pix_fmt='yuv420p',
                shortest=None
            )
            
            runner.run(overwrite_output=True, quiet=True)
            return output_path

        except ffmpeg.Error as e:
            print(f"❌ Render Fail Scene {scene_id}: {e.stderr.decode('utf8') if e.stderr else str(e)}")
            return None

    def render_all_scenes(self, script_data, video_pairs):
        """
        Iterates script and renders every scene using its A/B video pair.
        """
        rendered_paths = []

        for i, scene in enumerate(script_data):
            current_pair = video_pairs[i]
            if current_pair is None:
                continue

            output_path = self.process_scene(scene, current_pair)
            if output_path:
                rendered_paths.append(output_path)
        
        return rendered_paths

    def concatenate_with_transitions(self, video_paths, output_filename="final_short.mp4"):
        """
        Stitches rendered scenes together.
        INCLUDES FIXES FOR: Windows 0x80004005 Error & Playback Issues.
        """
        print("🎬 Stitching final video...")
        output_path = os.path.join(self.final_dir, output_filename)
        
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except:
                print("⚠️ Warning: Could not delete old file. It might be open in a player.")

        if not video_paths:
            return None

        input1 = ffmpeg.input(video_paths[0])
        v_stream = input1.video
        a_stream = input1.audio
        
        current_dur = self.get_duration(video_paths[0])

        for i in range(1, len(video_paths)):
            next_clip = ffmpeg.input(video_paths[i])
            next_dur = self.get_duration(video_paths[i])
            
            trans_dur = 0.5
            offset = current_dur - trans_dur
            
            effect = random.choice(self.transitions)
            print(f"   ✨ Transition {i}: '{effect}' at {offset:.2f}s")

            v_stream = ffmpeg.filter(
                [v_stream, next_clip.video], 
                'xfade', 
                transition=effect, 
                duration=trans_dur, 
                offset=offset
            )
            
            a_stream = ffmpeg.filter(
                [a_stream, next_clip.audio], 
                'acrossfade', 
                d=trans_dur
            )
            
            current_dur = (current_dur + next_dur) - trans_dur

        # current_dur is now the full stitched length, which is what the CTA
        # needs in order to sit on the final seconds.
        v_stream = self._cta_filter(v_stream, current_dur)

        try:
            runner = ffmpeg.output(
                v_stream, 
                a_stream, 
                output_path, 
                vcodec='libx264',   # Standard H.264 video
                acodec='aac',       # Standard AAC audio
                pix_fmt='yuv420p',  # 🔥 FIX 1: Windows compatibility
                movflags='faststart', # 🔥 FIX 2: Corruption fix
                preset='medium' 
            )
            
            runner.run(overwrite_output=True, quiet=False)
            
            print(f"✅ FINAL VIDEO SAVED: {output_path}")
            return output_path

        except ffmpeg.Error as e:
            error_log = e.stderr.decode('utf8') if e.stderr else str(e)
            print(f"❌ Stitching Error: {error_log}")
            return None