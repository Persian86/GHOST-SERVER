# Ghost Unplugged — setting up the AI PC

Unplugged needs three things on the AI PC: Ollama (the local brain), Whisper and Piper. **On the AI PC all three are
already set up** (Whisper and Piper are in `docker-compose.yml`). To set them up somewhere else, Home Assistant's
standard voice services work — for example with the GPU:

```yaml
# add to /opt/ghost/docker-compose.yml (or a compose file of their own)
  whisper:                                   # hearing: speech → text, on the RTX 3060
    image: lscr.io/linuxserver/faster-whisper:gpu
    container_name: whisper
    restart: unless-stopped
    environment:
      - TZ=Australia/Melbourne
      - WHISPER_MODEL=small.en               # good on a 3060; "medium.en" if you want more accuracy
      - WHISPER_LANG=en
    volumes:
      - /opt/ghost/whisper:/config
    ports:
      - 10300:10300
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  piper:                                     # speaking: text → speech (fast on the CPU)
    image: lscr.io/linuxserver/piper:latest
    container_name: piper
    restart: unless-stopped
    environment:
      - TZ=Australia/Melbourne
      - PIPER_VOICE=en_GB-alan-medium        # pick any voice from rhasspy/piper-voices
    volumes:
      - /opt/ghost/piper:/config
    ports:
      - 10200:10200
```

Then `docker compose up -d whisper piper`, open the Ghost admin page → Brain & keys →
**Ghost Unplugged** → **Test**. Leave the addresses empty when Whisper and Piper run on the
same PC as Ollama.

Home Assistant can use the same two services for Assist (Settings → Devices & services →
Add → Wyoming Protocol → host = the AI PC, port 10300 / 10200).

## What changes while unplugged

| | Plugged in | Unplugged |
|---|---|---|
| Thinking | Claude (local model as backup) | local model only |
| Room screens' voice | Gemini Live | Whisper → local model → Piper |
| Phone / tablet voice | Gemini Live | needs the apps' Unplugged update; typed chat works |
| Camera watch, Guard mode descriptions | Claude | local model |
| Away mode Gemini key for phones | shared (if on) | not shared |
