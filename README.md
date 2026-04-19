# Humanode Validator Docker Image

Single-container Docker image running a Humanode mainnet validator and other services because there is no official docker image. 

## Deploy

1. **Clone and set the variables.**

   ```sh
   git clone
   ```

2. **Build the image + create the data volume.**

   ```sh
   docker compose build
   docker volume create hmnd-data
   ```

3. **Insert your session-key seed (one-shot, out-of-band).**

   ```sh
   read -rsp 'Seed: ' SEED; echo; printf '%s' "$SEED" | docker run --rm -i -v hmnd-data:/data hmnd-validator:test insert-key; unset SEED
   ```

4. **Bring the validator up.**

   ```sh
   docker compose up -d
   ```
5. **Scan a bioauth QR.**

   When you get a bioauth DM (or send `/link` yourself), tap the URL. It opens
   `https://webapp.mainnet.stages.humanode.io/open?url=<wss-ngrok-url>`. Scan
   your face. Done.