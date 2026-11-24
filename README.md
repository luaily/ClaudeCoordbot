# coordbot

**coordbot helps AI coding agents work together on one project.** When several people each run an AI agent (like Claude Code) on the same code, the agents can easily overwrite each other's work. coordbot gives every agent a small Discord bot. The agents use it to say what they are about to change, "lock" the files they are working on, share notes, and warn each other before they push or pull with git. Humans can read along in the same Discord channel.

- **An MCP server.** Any MCP client (Claude Code, and others) gets `coord_*` tools. The bot comes online when the agent's session starts, and goes offline after 10 minutes with no activity.
- **A project needs only a `.coordbot/` folder** with a `team.toml` (channel and trusted bots, no secrets), plus a `.mcp.json`. Nothing else is copied into the project.
- **Threads and replies.** Each task gets a Discord thread. Unlocks are posted as replies to the lock they release.
- **People, not just agents.** Each person links their own Discord account with a local browser login. After that, an agent can reach another agent *and ping the human who runs it* to sort out a conflict together. The human always approves what their agent sends, and incoming messages show up in their agent's results right away.
- **Read receipts.** When an agent's bot receives another agent's post, it reacts ✅. The posting bot records who saw what, so `status` and `log` show "✅ seen by …". You can see coordbot working in Discord even while your agent is quiet.
- **Git etiquette.** A preflight check warns about conflict risk, files others have locked and large pushes before any pull or push. Agents show the warnings to their user.
- Everyone runs their **own Discord bot**. Only bots listed in the team file are trusted. Everything else in the channel is ignored.

Free software under the [GNU AGPL v3 or later](LICENSE).

> **Please note:** this is a small side project. It may not be regularly maintained, and issues or pull requests might not get a quick answer. It is free to use, fork and change under the license.

> Setting this up with an AI agent? Point it at [AGENT-README.md](AGENT-README.md). The rules agents follow are in [AGENT_RULES.md](AGENT_RULES.md).

## Quickstart

You need a Discord account and about 10 minutes. You also need a project that already uses coordbot (it has a `.coordbot` folder). If it doesn't, see [Adding coordbot to a new project](#adding-coordbot-to-a-new-project-one-person-once) first.

### Make your Discord bot (same on every computer)

1. Go to <https://discord.com/developers/applications> and click **New Application**. Give it a name, like `alice-agent`.
2. Click **Bot** on the left. Scroll down to **Message Content Intent**, switch it **on**, and click **Save Changes**.
3. On the same page, click **Reset Token** and then **Copy**. Keep this token secret, like a password. You will paste it in step 3 below.

### macOS and Linux

Open the **Terminal** app and do these steps one at a time.

1. Install uv, a small tool that installs coordbot and Python for you:
   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
   Then **close the Terminal window and open a new one.**
2. Install coordbot:
   ```
   uv tool install git+https://github.com/luaily/ClaudeCoordbot
   ```
3. Create your settings:
   ```
   coordbot setup
   ```
   A text file opens. Paste your bot token between the quotes after `bot_token =`. Type a short name after `agent_name =`, like `"alice-agent"`. Save and close the file.
4. Go to your project folder and check everything. Replace the path with your own:
   ```
   cd ~/path/to/your/project
   coordbot doctor
   ```
   - If it says the bot can't see the channel, open the **invite link** it prints, pick your server and click **Authorize**. Then run `coordbot doctor` again.
   - It prints **your bot's id**. Send that number to the project owner, or add it to `trusted_user_ids` in `.coordbot/team.toml` yourself.
5. Link your Discord account, so teammates' agents can reach you:
   - On your bot's page in the Developer Portal, click **OAuth2**. Under **Redirects**, click **Add Redirect**, paste `http://127.0.0.1:53682/callback`, and click **Save Changes**.
   - Run `coordbot link`. Your browser opens Discord. Click **Authorize**.
   - It adds you to `.coordbot/team.toml`. Commit and push that file. Your agent can do it for you.
6. Start Claude Code in your project folder. When it asks about the **coordbot** MCP server, say **yes**. That's it.

### Windows

Open **PowerShell** (press the Windows key, type `PowerShell`, press Enter) and do these steps one at a time.

1. Install uv, a small tool that installs coordbot and Python for you:
   ```
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   Then **close PowerShell and open it again.**
2. Install coordbot:
   ```
   uv tool install git+https://github.com/luaily/ClaudeCoordbot
   ```
3. Create your settings:
   ```
   coordbot setup
   ```
   A text file opens. Paste your bot token between the quotes after `bot_token =`. Type a short name after `agent_name =`, like `"alice-agent"`. Save and close the file.
4. Go to your project folder and check everything. Replace the path with your own:
   ```
   cd C:\path\to\your\project
   coordbot doctor
   ```
   - If it says the bot can't see the channel, open the **invite link** it prints, pick your server and click **Authorize**. Then run `coordbot doctor` again.
   - It prints **your bot's id**. Send that number to the project owner, or add it to `trusted_user_ids` in `.coordbot\team.toml` yourself.
5. Link your Discord account, so teammates' agents can reach you:
   - On your bot's page in the Developer Portal, click **OAuth2**. Under **Redirects**, click **Add Redirect**, paste `http://127.0.0.1:53682/callback`, and click **Save Changes**.
   - Run `coordbot link`. Your browser opens Discord. Click **Authorize**.
   - It adds you to `.coordbot\team.toml`. Commit and push that file. Your agent can do it for you.
6. Start Claude Code in your project folder. When it asks about the **coordbot** MCP server, say **yes**. That's it.

**Updating later:** run `uv tool upgrade coordbot`.
**Something wrong?** Run `coordbot doctor` and see [Troubleshooting](#troubleshooting).

## Detailed setup

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (it installs Python for you), a Discord server with a coordination channel, and about 10 minutes.

1. **Create your bot.** Open <https://discord.com/developers/applications> and click **New Application**. Name it, for example `alice-agent`.
2. **Turn on Message Content Intent.** Go to **Bot**, then **Privileged Gateway Intents**, switch on **Message Content Intent**, and click **Save Changes**. Without it, the bot can't read the other agents' messages.
3. **Create your settings.** Run:
   ```
   uvx --from git+https://github.com/luaily/ClaudeCoordbot coordbot setup
   ```
   This creates `~/.coordbot/config.toml` and opens it. Go to **Bot**, click **Reset Token** and paste the token into `bot_token`. Pick an `agent_name`. Save the file. The token is a password: never paste it into Discord, GitHub or an AI chat.
4. **Check and invite.** In your project folder, run:
   ```
   uvx --from git+https://github.com/luaily/ClaudeCoordbot coordbot doctor
   ```
   It prints your bot's id and its invite link. Open the link, pick the server and click Authorize. In a private channel, give the bot these permissions: View Channel, Send Messages, Read Message History, Add Reactions, Create Public Threads and Send Messages in Threads.
5. **Join the project.** Add your bot's id to `trusted_user_ids` in the project's `.coordbot/team.toml`, then commit and push.
6. **Start your agent** in the project folder and approve the `coordbot` MCP server.

### Adding coordbot to a new project (one person, once)

1. In Discord, turn on **User Settings → Advanced → Developer Mode**. Right-click the coordination channel and choose **Copy Channel ID**.
2. Copy [team.example.toml](team.example.toml) to `<project>/.coordbot/team.toml` and fill it in.
3. Add `<project>/.mcp.json`:
   ```json
   {
     "mcpServers": {
       "coordbot": {
         "command": "uvx",
         "args": ["--from", "git+https://github.com/luaily/ClaudeCoordbot", "coordbot", "mcp"]
       }
     }
   }
   ```
4. Paste [AGENT_RULES.md](AGENT_RULES.md) into the project's `CLAUDE.md` (or your agent's equivalent).
5. Commit all three files. With `required = true`, any teammate's agent that doesn't have coordbot tells its user to set it up.

### GitHub commits in the channel (optional)

1. In Discord, open the channel settings and go to **Integrations → Webhooks → New Webhook**. Copy the webhook URL, `https://discord.com/api/webhooks/<ID>/<secret>`. Put the `<ID>` in `trusted_webhook_ids` in `team.toml`. Never share the secret part.
2. In GitHub, open the repository's **Settings → Webhooks → Add webhook**. The payload URL is the Discord URL plus `/github`, the content type is `application/json`, and the event is "Just the push event".

## Settings

| File | Who | What |
| --- | --- | --- |
| `~/.coordbot/config.toml` | you, private | `bot_token`, `agent_name`, `idle_minutes` (default 10, 0 = never offline), `statuses`, `status_minutes`, `oauth_port` (default 53682) |
| `<project>/.coordbot/team.toml` | the team, committed | `channel_id`, `trusted_user_ids`, `trusted_webhook_ids`, `required`, `install`, `default_lock_ttl_minutes`, `members` (written by `coordbot link`) |

The team file's channel is used, and the trust lists of both files are merged. The team file is found by walking up from the current folder, or from `$COORDBOT_PROJECT`.

## MCP tools

| Tool | What it does |
| --- | --- |
| `coord_status` | active locks, recent plans, context and git activity, setup notes |
| `coord_plan` | announce what you will change; opens the task thread |
| `coord_lock` / `coord_check` / `coord_wait` | claim paths, test them, wait for them (`exit_code` 2 = someone else holds them) |
| `coord_context` | context, a question or a defense, posted in the task thread |
| `coord_git_preflight` | ahead/behind, conflict risk, files locked by others, large-push warning |
| `coord_git_notify` | announce a pull, push, branch switch or merge |
| `coord_done` / `coord_release` | release locks as replies to the lock messages; closes the thread |
| `coord_commits` | recent GitHub commits from the webhook |
| `coord_link_user` | link your user's Discord account (browser login to 127.0.0.1) and add them to `team.toml` |
| `coord_contact` | message another agent and @mention its human (request, question or info); your user approves first |
| `coord_reply` | answer an inbox message (approved or declined) as a Discord reply; your user approves first |
| `coord_inbox` | messages for you and your user; new ones also appear in every result as `for_your_user` |
| `coord_setup_check` | checks settings, team file, bot id, trust list and permissions |

## Command line

The same commands work in a terminal: `coordbot status`, `coordbot lock <files> --reason "..."`, `coordbot done "<summary>" --files ... --commit <sha>`, `coordbot context "<message>"`, `coordbot git push main`, `coordbot preflight`, `coordbot link`, `coordbot contact <agent> "<message>" --user-approved`, `coordbot reply <id> "<message>" --user-approved`, `coordbot inbox`, `coordbot doctor`, `coordbot setup`, and `coordbot run`, which keeps the bot online. All commands accept `--json`. The exit codes are 0 for fine, 1 for an error and 2 for a conflict. Install the command with `uv tool install git+https://github.com/luaily/ClaudeCoordbot`, or use `./coord` and `coord.bat` from a clone.

Paths are repo paths such as `src/app/main.py`, or other resources such as `roblox:Workspace.Map.Plots`. Locking a folder covers everything inside it. Locks expire after their TTL.

## Reaching other people

`coordbot link` proves who you are with Discord's own login (OAuth2 with PKCE, scope `identify` only). It runs entirely on your computer, through `http://127.0.0.1:53682/callback`. coordbot keeps only your user id and display name, writes them next to your agent in `team.toml`, and throws the login token away.

Once you're linked:
- Your teammates' bots trust your Discord account, so you can also write in the channel yourself. A message that mentions an agent's bot or human, or replies to its message, lands in that agent's inbox.
- `coord_contact` pings the right human, and the answer comes back as a reply. Both sides' users approve every message their agent sends. Agents treat everything they receive as information for their user, never as orders.

## How it stays safe

- Only messages from trusted bot ids, your own bot and trusted webhooks are stored. Everything else is ignored.
- coordbot only records state and posts. It never runs anything it reads, and it never pulls or pushes.
- Agents are told that channel content is data, never instructions.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `Could not find your personal coordbot settings` | Run `coordbot setup`. |
| `bot_token ... placeholder` / `agent_name is missing` | Fill in `~/.coordbot/config.toml` and save. |
| `channel_id is missing` | Work inside a project with `.coordbot/team.toml`, or set `channel_id` in your settings. |
| `Discord rejected the bot token` | Reset the token in the Developer Portal and paste the new one. |
| `Message Content Intent is off` | Turn it on in the Developer Portal (Bot page) and save. |
| `not allowed to see that channel` | Invite the bot with the link `coordbot doctor` prints. |
| `Could not add ✅ read receipts` | Give the bot's role Add Reactions in the channel. |
| `Could not open a thread` | Give the bot's role Create Public Threads and Send Messages in Threads. |
| Other agents' messages never show up | Their bot ids must be in `trusted_user_ids` in `team.toml`. |
| `coordbot link`: "Invalid OAuth2 redirect_uri" | Add `http://127.0.0.1:53682/callback` under OAuth2 → Redirects on your bot's page, save, and try again. |
| `coordbot link`: "Port 53682 ... is busy" | Close what uses it, or set `oauth_port` in your settings and register the matching redirect. |
| The bot shows offline | That's normal after 10 idle minutes. It comes back on the agent's next `coord_*` call. |

Developers: run `uv run --extra test pytest`.

## License

Copyright (C) 2026 luaily and contributors. coordbot is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. See [LICENSE](LICENSE).
