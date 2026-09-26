# Deploying the RC practice app

This project supports local SQLite by default and PostgreSQL when `DATABASE_URL`
is configured. Do not commit database files, PDF source material, or deployment
secrets.

## 1. Create the hosted database

Create a PostgreSQL database with a provider such as Supabase or Neon. Copy its
connection string, including SSL settings. Use the provider's pooled connection
string if it offers one.

## 2. Push the application to your GitHub account

Create a private GitHub repository and add the Python source files plus
`requirements.txt`. Keep the included `.gitignore` so local databases, PDFs,
and secret files are not uploaded. Do not add `rc_mastery.db`,
`prep_master.db`, or the GMAT PDFs to the repository.

## 3. Deploy with Streamlit Community Cloud

In Streamlit Community Cloud, create an app from your GitHub repository and
select `app.py` as the entry point.

In the app's **Settings → Secrets**, add:

```toml
APP_ENV = "production"
DATABASE_URL = "postgresql://USER:PASSWORD@HOST:PORT/DATABASE?sslmode=require"
ADMIN_EMAIL = "your-admin-email@example.com"
ADMIN_PASSWORD = "use-a-unique-long-password"
```

Use the exact connection string supplied by your database provider. Keep these
values private. The app creates the tables and initial administrator on startup.
The app refuses to start in production without `DATABASE_URL`, so it cannot
silently fall back to an ephemeral SQLite database. Public registration creates
student accounts only; administrator accounts are bootstrapped from these
secrets.

## 4. Add RC content

Sign in with the configured administrator account and upload the passage,
question, and answer-key PDFs from **Admin Panel → Separate PDFs**. They are
imported into PostgreSQL, so the passage content remains available across app
restarts without placing the PDFs or a local database in GitHub.

## 5. Share and index the app

Streamlit Community Cloud provides a public app URL after deployment. Anyone
with that URL can reach the app unless access is restricted in the hosting
settings. Public availability does not guarantee that a search engine will
index it; submit the deployed URL through the search engine's webmaster tools
if you want to request indexing.
