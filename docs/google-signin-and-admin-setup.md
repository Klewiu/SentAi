# Google sign-in and administrator setup

Implemented locally on 6 September 2026. Google sign-in is disabled until `GOOGLE_CLIENT_ID` is configured. No client secret or Gmail mailbox permission is required.

## Enable Google sign-in

1. In Google Cloud, configure the OAuth consent screen/Google Auth Platform branding and audience for the app. During testing, add the intended test users.
2. Create an OAuth client with application type **Web application**. Register each exact **Authorized JavaScript origin**, including protocol and port: for example `http://localhost:8000`, `http://127.0.0.1:8000`, and your production HTTPS origin. Use only origins you control.
3. Set `GOOGLE_CLIENT_ID=...apps.googleusercontent.com` in the app environment and restart the server. The login and registration pages will show Google's standard sign-in button. This implementation uses the GIS JavaScript callback and a same-origin CSRF-protected form, so it does not require an OAuth authorization-code redirect URI or client secret.
4. Test the actual Google popup on each registered origin. Check signup, returning login, explicit linking from the profile, cancellation, and administrator redirection to the second-factor check. Google may require consent-screen verification depending on audience and branding. Follow its console's requirements before making the integration public.

Google's maintained Python library verifies ID-token signatures, the configured audience, issuer and expiry. The app additionally checks a short-lived, session-bound nonce and verified Gmail/Google Workspace email. It stores Google's stable subject ID rather than using email as the returning-login identifier. ID tokens are not stored. Existing local accounts must sign in and explicitly link Google from their profile; matching email alone cannot merge or activate an account. Disabled accounts remain disabled.

This confirms account identity/email. It does not establish company/domain ownership, certification, or a completed company review. A normal Google ID token also does not prove a second factor was used, so administrator MFA remains enforced.

Official integration references: [Google server verification](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token), [Google HTML configuration](https://developers.google.com/identity/gsi/web/reference/html-reference).

## Administrator experience

An administrator with no authenticator is now directed to a browser setup wizard. They confirm their current password, scan a locally generated QR code with Google Authenticator, Microsoft Authenticator or a compatible password manager, and enter the current six-digit code. The setup expires after ten minutes. No QR service receives the secret. The response prohibits caching.

After enrollment, ten single-use recovery codes are displayed once. Store these in a password manager before continuing. A logged-in administrator subsequently enters only their authenticator code (or selects a recovery code), without retyping username/password. Setup cannot replace an existing confirmed authenticator. Trusted-server enrollment remains available for recovery or special provisioning; a Google-only account promoted to administrator needs a local password or trusted-server provisioning before its initial enrollment.

Normal customer accounts do not receive the administrator MFA prompt. Google sign-in does not bypass staff/admin MFA. Google sign-in and Google Authenticator are separate features.

## Verification and rollout

The local database was backed up before applying `accounts.0009_googleidentity`. Automated checks cover signed JWT validation (audience, issuer, expiry, signature), CSRF, nonce mismatch/replay, email verification, explicit linking, disabled accounts, admin MFA enforcement, QR enrollment, and consuming recovery codes. Google credentials have not been supplied, so actual Google popup/consent verification remains pending.

Install the updated `requirements.lock`, run migrations on the deployment database after a backup, and configure the web client ID. Keep the production controls and deployment requirements in `security-repairs.md`.
