# GitHub Publish Checklist

Previous repository style reference:

- GitHub account: your GitHub account or organization
- Default branch: `main`
- Commit message style: short imperative summaries, for example
  `Prepare VibeMotion for public repository`
- Keep heavy runtime media, model files, logs, and private projects out of Git

Before publishing this repository:

1. Run `python scripts/audit_publication.py`.
2. Review `NOTICE.md`, `docs/license_review.md`, and `THIRD_PARTY_NOTICES.md`.
3. Confirm `.env`, `.secrets/`, `models/`, `projects/`, `output/`, `qa_artifacts/`, `vendor/`, generated style presets, and generated Figma assets are not staged.
4. Keep credentials only in local `.env` or OS environment variables.
5. Do not commit rendered videos, local project JSON files, downloaded model weights, Figma exports, QA screenshots, or local browser traces.
6. Confirm `git status --short --ignored=matching` shows private/heavy folders as ignored.
7. If a secret was ever committed to a real Git repository, rotate it before pushing.

Suggested first-time Git setup:

```powershell
git init  # skip if already initialized
git branch -M main
git add .gitignore .gitattributes .github .env.example LICENSE NOTICE.md README.md THIRD_PARTY_NOTICES.md pyproject.toml app docs figma-plugin scripts projects/.gitkeep style_presets/.gitkeep Launch-VibeMotion.bat Launch-VibeMotion-Visible.bat Stop-VibeMotion.bat
python scripts/audit_publication.py
git status --short
git commit -m "Prepare VibeMotion for public repository"
```

After creating the GitHub repository under the same account:

```powershell
git remote add origin https://github.com/<owner>/<repo-name>.git
git push -u origin main
```
