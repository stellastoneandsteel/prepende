# Prepende vault template

This is the sanitized seed for a new Prepende installation. It contains the
folder structure and review rules, but no operator, customer, or company data.

Initialize a private vault with `knowledge.bootstrap.initialize_vault`. Keep the
result outside the source tree or in an ignored runtime directory. Obsidian is
an optional viewer; Prepende reads these plain Markdown files directly.

Open the initialized `vault/` directory directly in Obsidian. The included
minimal settings keep new notes in `wiki/`, attachments in `raw/`, and preserve
wikilinks; Obsidian remains a viewer rather than a runtime dependency.
