### Added
- **Hourly and weekly spend limits, and limits for a group and the whole team.** Beside daily and monthly, a bot's limit
  and the team default can now cap an hour (the UTC clock hour) and a week (from Monday 00:00 UTC). New pooled limits cap
  the combined spend of every bot (Usage > **Team and group limits**) or of a group's bots, nested groups included; over
  one, every bot it covers takes no new job until the period turns over or the limit is raised, and reads "Paused: the
  team is over its hourly limit". The owner and administrators set them; 80% and 100% notices go to the team owner.
  `PUT /api/v2/usage/pools/company`, `PUT /api/v2/usage/pools/groups/{group}`; `GET /api/v2/usage/limits` adds the
  hourly and weekly fields and, for administrators, `pools`. A bot's limit saved from an older page clears its hourly
  and weekly caps, as any field left out does.
