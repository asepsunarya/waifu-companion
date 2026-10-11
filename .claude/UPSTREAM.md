# Upstream

Konten di .claude/skills, .claude/references, .claude/agents, .claude/commands,
dan .claude/hooks di-generate dari:

- Repo    : https://github.com/addyosmani/agent-skills
- Versi   : 0.6.12
- Commit  : 1be8e34 (2026-10-10)
- Lisensi : MIT (c) Addy Osmani

Jangan edit file di folder-folder itu secara manual. Untuk memperbarui,
jalankan `scripts\update-from-upstream.ps1` di repo claude-code-starter lalu
salin ulang ke project ini (`install.ps1 -Project <path> -Force`).
Kustomisasi lokal sebaiknya ditaruh di CLAUDE.md atau .claude/rules/.