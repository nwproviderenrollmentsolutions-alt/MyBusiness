# What to upload to unlock the avatar

`ugc_cloner` refuses to generate a presenter until this checklist is done — see
`../ugc_cloner/avatar.py`'s `require_avatar()`. Nothing gets invented in place of these
files; every code path reports `AVATAR REQUIRED` until they're here.

Commit everything below straight into this `avatar/` folder (never paste it in chat —
these are the actual files the pipeline reads).

## 1. Reference photo or video — `reference-images/` or `reference-video/`

This is what trains the avatar's face, so the vendor recommendation below (HeyGen) has
firm requirements:

- **Photo avatar** (fastest, cheapest): one clear, forward-facing photo, good even
  lighting, neutral or slight smile, no sunglasses/hat, at least 512x512px. A phone photo
  is fine if it's in focus and not backlit.
- **Video avatar** (more natural motion, higher fidelity): 2+ minutes of you talking to
  camera, 1080p or better, one continuous shot, consistent lighting and background,
  looking at the lens, minimal head movement out of frame.
- Drop the file in `reference-images/` or `reference-video/` in this folder. Multiple
  angles/takes are fine — the pipeline lists every file it finds.

## 2. Voice sample — `voice/`

For the recommended pairing (ElevenLabs voice cloning):

- 1–3 minutes of clean speech, you alone talking (no music, no crosstalk, minimal room
  echo), any topic — reading something out loud works.
- A phone voice memo or the audio track from the same video above is enough; it does not
  need to match the reference video's content.
- Drop it in `voice/`.

## 3. Fill in `avatar-profile.md`

Open `avatar-profile.md` in this folder and fill in the YAML block: at minimum
`avatar_identity.name`, and `identity_lock.enabled: true` /
`identity_lock.replacement_allowed: false` (already the template default — leave them).
The `appearance`, `wardrobe`, `personality`, and `camera` fields aren't required to
unlock generation, but the more you fill in, the more consistent every generated prompt
will be.

## 4. Pick and train the vendor (one-time, outside this repo)

Once the files above exist, `python -m ugc_cloner.cli avatar-status` will confirm the
lock is satisfied — but rendering still uses the mock provider until a real vendor is
trained and wired up:

**Recommended pairing** — see `../providers/heygen_video_gen.py` and
`../providers/elevenlabs_voice.py` for why these two specifically (durable, named
identities rather than a re-described face/voice on every call, which is what the avatar
lock's whole design depends on):

| Step | Where | What you get |
|---|---|---|
| Train a HeyGen avatar from your reference photo/video | heygen.com dashboard (Avatars > Create Avatar) | an `avatar_id` |
| Clone your voice in ElevenLabs from your voice sample | elevenlabs.io dashboard (Voices > Add Voice > Instant Voice Cloning) | a `voice_id` |
| Set both in `.env` | this repo | real rendering, not mock |

```bash
VIDEO_GEN_PROVIDER=heygen
HEYGEN_API_KEY=...
HEYGEN_AVATAR_ID=...      # from the HeyGen step above

VOICE_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...   # from the ElevenLabs step above
```

Both training steps happen once, in each vendor's own dashboard, from the exact files you
uploaded in steps 1–2 — this repo never trains a vendor avatar itself, it only calls the
`avatar_id`/`voice_id` you set once training is done.
