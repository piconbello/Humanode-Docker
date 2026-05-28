# Humanode Docker Image

Single-container Docker image running a Humanode mainnet node.

## What you need

- Docker and Docker Compose.
- Your session-key seed mnemonic (**not** your stash/controller seed — session keys only).
- An [ngrok](https://ngrok.com) account and auth token (free tier works).

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SYNC_MODE` | `full` | Sync strategy: `full`, `warp`, `fast`, `fast-unsafe`. |
| `NODE_NAME` | auto-generated | Suffix for the network display name. Always prefixed with `HND-`. Auto-generates `HND-<word>-<hex>` if unset; setting `NODE_NAME=mynode` produces `HND-mynode`. |
| `NGROK_AUTHTOKEN` | _(empty)_ | [ngrok](https://ngrok.com) auth token. Required for bioauth — tunnels the node's RPC so the bioauth webapp can reach it. |

## Deploy

1. **Clone + configure.**

   ```sh
   git clone https://github.com/piconbello/Humanode-Docker.git
   cd Humanode-Docker
   cp .env.example .env
   # set NGROK_AUTHTOKEN in .env
   ```

2. **Build the image + create the data volume.**

   ```sh
   docker compose build
   docker volume create hmnd-data
   ```

   Or pull from GHCR instead of building:

   ```sh
   docker pull ghcr.io/piconbello/humanode-docker:latest
   ```

3. **Insert your session-key seed (one-shot, out-of-band).**

   ```sh
   read -rsp 'Seed: ' SEED; echo; printf '%s' "$SEED" | docker run --rm -i -v hmnd-data:/data hmnd-validator:latest insert-key; unset SEED
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

## Finding your node name

If you didn't set `NODE_NAME`, the container auto-generates one on first boot
and persists it across restarts. To find it:

```sh
docker compose logs humanode 2>&1 | grep 'node-name:'
```

You can look up your node on the [Humanode Telemetry](https://telemetry.humanode.io) dashboard by searching for this name.

## Bioauth

Validators must complete periodic face authentication ("bioauth"). When
`NGROK_AUTHTOKEN` is set, the container opens an ngrok tunnel to the node's RPC
and prints the bioauth webapp link to the logs:

```sh
docker compose logs humanode 2>&1 | grep 'bioauth link:'
```

Open that URL in a browser, scan your face, done.
