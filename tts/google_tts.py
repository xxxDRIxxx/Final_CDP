from gtts import gTTS
import tempfile
import os
import playsound

def speak_text(text: str):
    try:
        # Create a temporary file path (not locked by context manager)
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        tmp_file.close()  # close so gTTS can write to it

        # Save TTS to the file
        tts = gTTS(text=text, lang="en")
        tts.save(tmp_file.name)

        # Play the audio
        playsound.playsound(tmp_file.name)

        # Delete after playing
        os.remove(tmp_file.name)

    except Exception as e:
        print(f"⚠️ Error in Google TTS: {e}")
   