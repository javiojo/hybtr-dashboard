# MailerLite signup: rotate the exposed token and point the form at a proxy

The public `index.html` previously called `https://connect.mailerlite.com/api/subscribers` from the browser with a **bearer token in the page**. Anyone could copy that token from the live site or from git history and add (or, depending on the token's scope, manage) subscribers.

This pull request **removes the token from `index.html`**. Until you rotate it and deploy the small proxy, the on-site signup box will show a short friendly error instead of talking to MailerLite. The EmailJS contact form is unchanged.

Do not put the old or new token in a chat, a pull request, or a commit.

## You should do these things, in order

### 1. Revoke the exposed token now

The old token is already public: it is in git history on this public repo, and it stays on the live GitHub Pages site until this change is merged **and** the token is revoked. Rewriting git history is **not** recommended (it will not un-copy the token, and it would disrupt the daily data job).

1. Sign in to [MailerLite](https://www.mailerlite.com/).
2. Open your API / developer / integrations settings and **revoke or delete** the existing API token (the one that was embedded in the site).
3. Create a **new** token with only the **subscribers** (or equivalent "add/update subscribers") scope. Do not grant campaign, account, or group-admin scopes if you can avoid it.
4. Copy the new token into a password manager. Do not paste it into this repository.

Until you revoke the old token, anyone who already copied it can still use it, even after this pull request is merged.

### 2. Deploy the proxy (needs a one-time okay)

`subscribe-proxy/` is a tiny Vercel serverless function with no npm dependencies. It is **not** deployed by this pull request, and no Vercel project has been created.

When you are ready:

1. Create a new Vercel project from the `subscribe-proxy/` folder (a free hobby project is enough). You can also add a `subscribe-proxy` root-directory project in the existing Vercel account. Do not hook it up to the GitHub Pages site; it is a separate tiny service.
2. Set an environment variable on that project:
   - Name: `MAILERLITE_TOKEN`
   - Value: the **new** token from step 1
   - Optional: `MAILERLITE_GROUP` = `187947608914789971` (the existing group the site already uses; not a secret).
3. Deploy. Note the URL of the function, which will look like `https://<your-project>.vercel.app/api/subscribe`.
4. In `index.html`, set the constant at the top of the main script:

   ```js
   const SUBSCRIBE_ENDPOINT = 'https://<your-project>.vercel.app/api/subscribe';
   ```

   Commit that one-line change (or ask for a follow-up PR). After Pages deploys, the signup box and the contact-form "subscribe" checkbox will start working again.

The proxy only accepts `POST` from `https://hybtretfs.com` and `https://www.hybtretfs.com`, validates the email, rate-limits lightly by IP, and never echoes the token.

### 3. No-code alternative (no Vercel project)

If you would rather not run a proxy:

1. In MailerLite, create a hosted or embedded signup form.
2. Copy its form id / embed snippet.
3. Replace the custom signup box in `index.html` with MailerLite's embed (or a link to the hosted form).
4. Still revoke the old API token (step 1). You will not need `SUBSCRIBE_ENDPOINT` or `MAILERLITE_TOKEN`.

This is the simplest option if you do not want another service to operate.

## What was left in the page on purpose

- `ML_GROUP` (`187947608914789971`) is a group id, not a credential. The proxy forwards it so new subscribers still land in the same group.
- EmailJS public keys stay in the page; they are designed to be public. Please lock the EmailJS template/service to `hybtretfs.com` in the EmailJS dashboard if that is not already done.
