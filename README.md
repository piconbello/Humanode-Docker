# Humanode Docker Image

Single-container Docker image running a Humanode mainnet node.

## What you need

- Docker and Docker Compose.
- Your session-key seed mnemonic (**not** your stash/controller seed — session keys only).

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SYNC_MODE` | `full` | Sync strategy: `full`, `warp`, `fast`, `fast-unsafe`. |
| `NODE_NAME` | `humanode-validator` | Display name on the network. |

## Deploy

1. **Clone + configure.**

   ```sh
   git clone <this repo>
   cd humanode
   cp .env.example .env
   # edit .env
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

   At the `Seed:` prompt, paste your 12/24-word mnemonic. It won't echo. On
   success you'll see `insert-key: keystore populated for key-type kbai`.

   The seed never enters:
   - shell history (`read -rs` doesn't write to history)
   - process argv (`--suri <file>` uses a tmpfs-backed file, not the command line)
   - the runtime container (only the keystore file lives on the volume)

4. **Bring the node up.**

   ```sh
   docker compose up -d
   docker compose logs -f humanode
   ```
