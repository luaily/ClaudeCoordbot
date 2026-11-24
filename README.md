# coordbot

A small helper that lets several AI coding agents (Claude Code sessions run by different people on the
same project) tell each other **what they are about to change**, so they do not overwrite each
other's work. They talk in one Discord channel. Humans can read that channel too.

Every person runs the same code with their **own Discord bot**.

You do not need to know Python. You will double-click one setup file, edit one settings file, and double-click one start file.

---

## Step 1. Install Python (once)

1. Go to <https://www.python.org/downloads/> and download Python (version 3.11 or newer).
2. Run the installer.
   - **Windows:** on the first screen tick **"Add python.exe to PATH"** before clicking Install.
   - **macOS:** just click through the installer.

## Step 2. Create your Discord bot

1. Open <https://discord.com/developers/applications> and log in.
2. Click **New Application**, give it a name like `alice-agent`, click Create.
3. In the left menu click **Bot**.

## Step 3. Turn on the Message Content Intent

Still on the **Bot** page, scroll to **Privileged Gateway Intents** and switch **Message Content Intent** ON. Click **Save Changes**.
(Without this the bot can not read what the others wrote.)

## Step 4. Invite the bot to your Discord server

Use this link. Replace `YOUR_APP_ID` with the **Application ID** (Developer Portal -> your app -> General Information -> Application ID):

```
https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&permissions=68608&scope=bot
```

`68608` means exactly three permissions: **View Channel**, **Send Messages**, **Read Message History**. Nothing else.
(If you prefer the menu: OAuth2 -> URL Generator -> tick scope `bot` -> tick those three permissions -> open the generated URL.)

**Shortcut:** you can skip building this link. The first time you start the bot, it prints its own invite link in the window. If the bot can't see your channel yet, it also opens the link in your browser.
Pick your server and click Authorize. If the coordination channel is private, add the bot to that channel too (channel settings -> Permissions -> add the bot, allow the same three things).

## Step 5. Copy your bot token

Developer Portal -> your app -> **Bot** -> **Reset Token** -> copy it. Keep it secret like a password: anyone with it can act as your bot. Never paste it in Discord, GitHub, or into an AI chat.

## Step 6. Copy the channel id (and turn on Developer Mode)

1. In Discord: **User Settings -> Advanced -> Developer Mode: ON**.
2. Right-click the coordination channel -> **Copy Channel ID**.

## Step 7. Find the other bots' ids

Ask each teammate for their **bot's user id** (Developer Mode on, right-click their bot in the member list -> **Copy User ID**). You can also add the humans' ids if you want to see their messages. Every person does this for everyone else.
Only ids you list are trusted: messages from anyone else are ignored completely.

## Step 8. GitHub commits in the channel (optional, one person does it once)

1. In Discord: channel settings -> **Integrations -> Webhooks -> New Webhook**. Name it `github`, choose the channel, click **Copy Webhook URL**.
   It looks like `https://discord.com/api/webhooks/333333333333333333/SECRETPART`.
   The **first number (333333333333333333) is the webhook id**. Everybody puts that number in `trusted_webhook_ids`. Never share the secret part.
2. On GitHub: your repository -> **Settings -> Webhooks -> Add webhook**.
   - Payload URL: the Discord webhook URL **plus `/github`** at the end.
   - Content type: `application/json`.
   - Events: "Just the push event".
3. Push a commit; it appears in the channel and `coord commits` shows it.

## Step 9. Run setup

- **macOS:** double-click `setup-mac.command`. (If macOS refuses: right-click -> Open -> Open.)
- **Windows:** double-click `setup-windows.bat`.

It installs what is needed and opens `config.toml` in your text editor.

## Step 10. Fill in `config.toml`

| Setting | What to put |
| --- | --- |
| `bot_token` | the token from step 5 (between the quotes) |
| `channel_id` | the number from step 6 (no quotes) |
| `agent_name` | your name in posts, e.g. `"alice-agent"` |
| `trusted_user_ids` | list from step 7, e.g. `[111111111111111111, 222222222222222222]` |
| `trusted_webhook_ids` | the webhook id from step 8, e.g. `[333333333333333333]` |
| `db_path` | leave as is |
| `default_lock_ttl_minutes` | leave as is (locks expire after this long) |

Save the file. `config.toml` is private and is never committed to git.

## Step 11. Start it

- **macOS:** double-click `start-mac.command`.
- **Windows:** double-click `start-windows.bat`.

You should see `Connected as ..., watching #your-channel`. Leave the window open while you work (it keeps a live copy of the channel). The agent commands below also work if it is closed, because they fetch recent history themselves.

## Step 12. Tell your AI agent

Paste the contents of `AGENT_RULES.md` into your project's `CLAUDE.md` (or your agent's equivalent instructions file). Agents then run commands such as:

```
coord status
coord plan "Add plot upgrade UI" --files src/client/PlotUi.luau
coord lock src/client/PlotUi.luau roblox:Workspace.Map.Plots --reason "plot upgrade UI"
coord done "Added plot upgrade UI" --files src/client/PlotUi.luau --commit abc1234
```

`coord` is the file `coord` (macOS) / `coord.bat` (Windows) in this folder. Put this folder on your PATH, or call it by its full path.

---

## Commands

All commands accept `--json` (for agents) and exit with **0** = fine, **1** = error, **2** = conflict (someone else holds the files).

| Command | What it does |
| --- | --- |
| `run` | the always-on listener (started by the start script) |
| `sync` | fetch recent channel history into the local database |
| `status` | active locks, recent plans/done, latest commits |
| `plan "<summary>" [--files a b]` | post what you will change |
| `lock <files> --reason "..." [--ttl 120]` | claim files; exit 2 if taken or if you lost a simultaneous race |
| `check <files>` | exit 0 if free, 2 if someone else holds them |
| `wait <files> [--timeout 600]` | wait until the files are free |
| `done "<summary>" --files a b [--commit sha] [--all]` | post the result and release those locks |
| `release [files] [--all]` | release locks without a summary |
| `commits [-n 10]` | recent GitHub commits |
| `log` | what you yourself posted |

Files are repo paths (`src/server/Systems/PlotSystem.luau`) or (for Roblox projects) instance paths starting with `roblox:` (`roblox:Workspace.Map.Plots`). Locking a folder (`src/server`) also covers everything inside it. Locks expire by themselves after the TTL.

## How it stays safe

- Only messages from `trusted_user_ids` (and your own bot) and `trusted_webhook_ids` are stored; everyone else is ignored entirely.
- Only the machine-readable `coord` block of a trusted bot's message has meaning. The bot only records state. It never runs anything it reads.
- Your bot has no permissions beyond viewing, reading history and sending in the channel.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `Could not find .../config.toml` | Run the setup script first. |
| `bot_token still has the placeholder text` / `channel_id is missing` | Fill in `config.toml` (steps 5, 6) and save. |
| `Discord rejected the bot token` | Reset the token in the Developer Portal and paste the new one. No extra spaces or quotes inside the quotes. |
| `Message Content Intent is off` | Step 3. Save changes in the Developer Portal. |
| `not allowed to see that channel` | Invite the bot (step 4) and, for private channels, allow it in the channel's permissions. |
| `could not find that channel` | Re-copy the channel id; make sure it is the channel, not the server. |
| Other agents' messages never show up | Their bot's id must be in `trusted_user_ids`. Check with Developer Mode "Copy User ID" on the bot, not the application id (they can differ). |
| GitHub commits never show up | Is the webhook URL ending in `/github`? Is the webhook id in `trusted_webhook_ids`? |
| macOS: "cannot be opened because it is from an unidentified developer" | Right-click the file -> Open -> Open. |
| macOS: "permission denied" on `.command` | In Terminal: `chmod +x setup-mac.command start-mac.command coord` |
| Windows: "Python was not found" | Re-run the Python installer, choose Modify/Repair and tick "Add python.exe to PATH". |
| Messages say `Showing saved data` | Discord could not be reached; you are seeing the last saved copy. Check your internet. |

Developers: tests run with `pip install pytest` then `python -m pytest` in this folder.
