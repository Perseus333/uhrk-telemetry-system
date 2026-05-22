# Local Development Setup


## !! Disclaimer:
Just like the base of this code base, the scripts to run this have been fully AI generated purely for utilitarian reasons.
**This is not a substitute for testing in the real hardware!** Ensure that whatever is tested here, is also tested in the Pi's where it is intended to run.

## With Docker (recommended)

1. Install docker
2. From the root directory run:
```bash
docker compose docker-compose.dev.yml up
```

3. To stop it run: 
```bash
docker compose docker-compose.dev.yml down
```

## Without docker

If you prefer to avoid Docker entirely, open three terminal windows:

1. Start tmux
2. Create 3 terminals and paste each of the code blocks in a different one:

  ```bash
  cd remote/uhrkgc/uhrk_site
  UHRK_GROUND_GPS_PORT=/dev/null \
  UHRK_NODE_TIME_SYNC_URLS="" \
  python3 uhrk_backend.py
  ```

  ```bash
  cd remote/uhrkgc/uhrk_site
  python3 -m http.server 8000
  ```

  ```bash
  python3 dev/dummy_sender.py
  ```

