# Google sign-in and private progress

The site still runs on GitHub Pages. Supabase supplies Google authentication
and the progress database. Only the login session is persisted in browser
storage; question progress is loaded from Supabase into memory. Guest marks
last until the page is refreshed or closed. Signing in loads account progress
and discards guest marks. Old browser marks and local progress.json files are
ignored, with no import.

## Connect a free project

1. Create a Supabase project at https://supabase.com/dashboard. Use the Free plan
   for this experiment. Keep your database password private.
2. In its SQL editor, run
   `supabase/migrations/202610300001_question_progress.sql` once. The table's
   row-level policies permit only authenticated users to read or change their
   own records. Anonymous visitors have no database access.
3. Follow the official Google provider setup:
   https://supabase.com/docs/guides/auth/social-login/auth-google . Create a
   Google OAuth client of type **Web application**, configure the consent screen,
   and put the Supabase callback URL shown in the provider settings in Google's
   **Authorized redirect URIs**. Store the Google client ID and client secret
   only in Supabase's Google provider settings and enable that provider.
   While Google's consent screen is in Testing, add your Google account as a
   test user; public use requires the appropriate publishing configuration.
4. In Supabase Auth URL Configuration, set the Site URL to
   `https://yehudashani.github.io/TestPrep/`. Add that same URL and
   `http://127.0.0.1:8765/` to the allowed Redirect URLs. Add the exact localhost
   URL separately if you use localhost or a different port. PKCE preserves the
   question hash and returns its authorization code in the query string.
5. Copy the project URL and its **publishable** API key. These are public browser
   configuration; never use a secret or service-role key. In PowerShell:

   ```powershell
   $env:SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
   $env:SUPABASE_PUBLISHABLE_KEY = "sb_publishable_YOUR_KEY"
   .\serve-physics3.ps1
   ```

   For a static preview, set the same variables before running
   `.venv\Scripts\python.exe -m pdf_splitter.build_site --out site`.
   The build embeds these public settings in index.html. Do not deploy until
   the checks below pass. Without settings, the viewer supports guest use only.

## Verify with real accounts before deploying

- Sign in as account A, mark a question done and hard, refresh, and confirm
  both marks return. Unmark it, refresh, and confirm it stays unmarked.
- Open a second browser/device as A and confirm the marks load. Changes in
  another open tab are loaded on refresh; there is no live cross-device feed.
- Sign out, confirm A's marks disappear, and mark a question as a guest.
  Confirm the notice appears once for the page, course switching retains guest
  marks, and refreshing discards them.
- Sign in as account B. Confirm A's progress and guest marks do not appear.
- Using authenticated API requests for A and B, verify that B cannot read A's
  rows and that insert/update attempts with A's user ID fail. Verify anonymous
  requests cannot read or write the table. UI checks alone do not prove privacy.
- Disconnect the network, change marks, and confirm the saving failure notice.
  Reconnect and retry; refresh to confirm the final marks were saved. Rapid
  toggles in one tab are serialized; between devices, the last saved edit wins.
- Test Google return to a shared question link and denied/cancelled sign-in.

Changes save immediately. Closing a browser is not a reliable opportunity to
save; pending writes trigger a best-effort leave warning. Failed writes stay
only in memory and can be retried; they do not survive a forced close.

The client uses the pinned Supabase JS SDK from jsDelivr. If it cannot load,
the viewer remains usable as a guest. Production sign-in requires network
access to that CDN, Google, and your Supabase project.
