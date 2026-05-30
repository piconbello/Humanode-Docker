# Humanode Docker Image

Single-container Docker image running a Humanode mainnet node.

## Run a node

```sh
git clone https://github.com/piconbello/Humanode-Docker.git
cd Humanode-Docker
docker compose up -d
```

Your node is now syncing. Check the logs:

```sh
docker compose logs -f humanode
```

## Run a validator

A validator needs an [ngrok](https://ngrok.com) auth token (free tier works)
and your session-key seed mnemonic (**not** your stash/controller seed — session
keys only).

1. **Set your ngrok token and start the node.**

   ```sh
   cp .env.example .env
   # set NGROK_AUTHTOKEN in .env
   docker compose up -d
   ```

2. **Insert your session key into the running node.**

   ```sh
   read -rsp 'Seed: ' SEED; echo
   printf '%s' "$SEED" | docker compose exec -T humanode /usr/local/bin/insert-key.sh
   unset SEED
   ```

   At the `Seed:` prompt, paste your 12/24-word mnemonic. It won't echo. On
   success you'll see `insert-key: keystore populated for key-type kbai`.

   The seed never enters shell history, process argv, or the runtime container.

3. **Complete bioauth.** The ngrok tunnel prints the bioauth link to the logs:

   ```sh
   docker compose logs humanode 2>&1 | grep 'bioauth link:'
   ```

   Open that URL in a browser, scan your face, done.

## Finding your node name

The container auto-generates a name like `HND-cedar-a1f3b2` on first boot and
persists it across restarts. To find it:

```sh
docker compose logs humanode 2>&1 | grep 'node-name:'
```

If you set `NODE_NAME=mynode` in `.env`, the name becomes `HND-mynode`.

You can look up your node on the [Humanode Telemetry](https://telemetry.humanode.io) dashboard by searching for this name.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SYNC_MODE` | `full` | Sync strategy: `full`, `warp`, `fast`, `fast-unsafe`. |
| `NODE_NAME` | auto-generated | Suffix for the display name. Always prefixed with `HND-`. |
| `NGROK_AUTHTOKEN` | _(empty)_ | [ngrok](https://ngrok.com) auth token. Required for bioauth. |
