# File-tab sample recording

The "Try a sample recording" button on the File tab loads the file at:

`static/samples/sample-en.m4a`

To enable the demo, place a 10–15s spoken English audio clip at that path. Until then, the button will show a "File not found" error.

Suggested workflow:
1. Open DashScribe, hit Record (or use the global hotkey), say a couple of sentences.
2. Find the resulting clip in the History tab and locate the audio source on disk.
3. Convert to `.m4a` if needed: `ffmpeg -i input.wav -c:a aac -b:a 96k sample-en.m4a`
4. Drop into `static/samples/sample-en.m4a` and commit.

The path is referenced from the WS `start_file_job` handler in `app.py` when the inbound `path` equals `__sample__`.
