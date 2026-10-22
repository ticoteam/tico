### Fixed
- **Muse Code turns work again on a Docker computer that runs bots as their own user.** Each Muse login now keeps
  its sessions in a folder of Tico's own (per subscription profile too), so a turn, including an automatic fallback
  to Muse, no longer fails at once with "failed to create session lock directory ... Permission denied". If that
  folder is there but the bot user can't write it, Settings > Computers marks Muse Code and names the folder, rather
  than asking you to sign in. Muse sessions from before this change start fresh.
