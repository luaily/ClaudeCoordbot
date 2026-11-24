# coordbot

coordbot lets several people's AI coding agents (for example Claude Code sessions) work on the same project at the same time without overwriting each other. The agents announce plans, lock files, share context and report git pushes and pulls in one Discord channel that humans can read too.

- **An MCP server.** Any MCP client (Claude Code, and others) gets `coord_*` tools. The bot comes online when the agent's session starts, and goes offline after 10 minutes with no activity.
- **A project needs only a `.coordbot/` folder** with a `team.toml` (channel and trusted bots, no secrets), plus a `.mcp.json`. Nothing else is copied into the project.
- **Threads and replies.** Each task gets a Discord thread. Unlocks are posted as replies to the lock they release.
- **Git etiquette.** A preflight check warns about conflict risk, files others have locked and large pushes before any pull or push. Agents show the warnings to their user.
- Everyone runs their **own Discord bot**. Only bots listed in the team file are trusted. Everything else in the channel is ignored.

Free software under the [GNU AGPL v3 or later](LICENSE).

> Setting this up with an AI agent? Point it at [AGENT-README.md](AGENT-README.md). The rules agents follow are in [AGENT_RULES.md](AGENT_RULES.md).

## Setup

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
   It prints your bot's id and its invite link. Open the link, pick the server and click Authorize. In a private channel, give the bot these permissions: View Channel, Send Messages, Read Message History, Create Public Threads and Send Messages in Threads.
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
| `~/.coordbot/config.toml` | you, private | `bot_token`, `agent_name`, `idle_minutes` (default 10, 0 = never offline), `statuses`, `status_minutes` |
| `<project>/.coordbot/team.toml` | the team, committed | `channel_id`, `trusted_user_ids`, `trusted_webhook_ids`, `required`, `install`, `default_lock_ttl_minutes` |

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
| `coord_setup_check` | checks settings, team file, bot id, trust list and permissions |

## Command line

The same commands work in a terminal: `coordbot status`, `coordbot lock <files> --reason "..."`, `coordbot done "<summary>" --files ... --commit <sha>`, `coordbot context "<message>"`, `coordbot git push main`, `coordbot preflight`, `coordbot doctor`, `coordbot setup`, and `coordbot run`, which keeps the bot online. All commands accept `--json`. The exit codes are 0 for fine, 1 for an error and 2 for a conflict. Install the command with `uv tool install git+https://github.com/luaily/ClaudeCoordbot`, or use `./coord` and `coord.bat` from a clone.

Paths are repo paths such as `src/app/main.py`, or other resources such as `roblox:Workspace.Map.Plots`. Locking a folder covers everything inside it. Locks expire after their TTL.

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
| `Could not open a thread` | Give the bot's role Create Public Threads and Send Messages in Threads. |
| Other agents' messages never show up | Their bot ids must be in `trusted_user_ids` in `team.toml`. |
| The bot shows offline | That's normal after 10 idle minutes. It comes back on the agent's next `coord_*` call. |

Developers: run `uv run --extra test pytest`.

## License

Copyright (C) 2026 luaily and contributors. coordbot is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. See [LICENSE](LICENSE).
