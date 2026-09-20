# Security repair record

Local repair and verification: 6 September 2026. Pricing, annual upgrade charging policy, and product packaging are unchanged. Changes are in the working tree; they have not been deployed.

Subsequent authorized product work is recorded in [profile-plan-improvements.md](profile-plan-improvements.md). It makes file formats available on every plan; expiration now removes paid content allowances while keeping basic public formats accessible. The repair results below describe the original security phase.

Administrator enrollment is now available through a browser wizard; Google sign-in setup is documented in [google-signin-and-admin-setup.md](google-signin-and-admin-setup.md). The trusted-console enrollment command below is an alternative, rather than a prerequisite for normal setup.

## Findings and repairs

| Finding | Repair and verification |
| --- | --- |
| Unsigned Stripe events could grant paid access | Missing webhook secret fails closed with 503; invalid signatures return 400. Regression tests sign payloads using HMAC and verify that rejected requests cannot activate a plan. |
| Webhook replay, transient errors, and stale events | Persistent event IDs deduplicate successful processing. Failed transactions return 503 for retry. Subscription events retrieve current Stripe state under a user lock. Tests cover retry, duplication, and an old active event after cancellation. |
| Upgrade could collect a separate payment and another subscription invoice | Upgrade modifies the existing subscription using `pending_if_incomplete`, no proration, and a new annual anchor. Retries reuse a pending invoice; the higher entitlement is granted only when the current Stripe price changes. Tests assert one modification and no separate Checkout payment. Old standalone upgrade events generate an admin review notification. |
| Checkout success could fabricate a subscription or activate despite a lookup error | Success verifies session ownership and retrieves an actual subscription. It does not perform an upgrade or grant a fallback plan. Tests cover unavailable Stripe, unpaid sessions, legacy callbacks, and repeated open checkout reuse. |
| Manual access expired only on a billing page visit | Shared access decisions check paid period expiry and manual payment grace deadlines at the time of access. The maintenance command reconciles stored state. An expired or overdue customer cannot obtain premium public formats without visiting the dashboard first. |
| Dashboard bypassed plan quotas | Form validation now uses the same feature matrix as the API. Saving locks the owner and rechecks the current entitlement. API creation checks quotas under locks. Tests include a downgrade between validation and saving. PostgreSQL row-lock concurrency still needs validation in the deployment environment. |
| Premium data stayed published after downgrade | Public resource builders limit products, tags, social profiles and entries by current entitlement; company descriptions are capped by language allowance. The site llms.txt index checks effective access rather than an outdated organization subscription row. Existing stored content is retained until the owner edits/removes it. |
| Stored XSS through embedded JSON-LD | Script-sensitive characters are escaped before JSON is embedded in HTML. A malicious closing-script payload is tested against the rendered page. URL parsing also rejects unsupported social hosts masquerading as supported domains. |
| Public feeds exposed private login email | JSON, JSON-LD and text output use the explicitly supplied business contact email. A regression test ensures the owner's private email is absent. |
| Edits retained verification and stale timestamps | Material organization edits and related content changes revoke review and clear reviewer metadata. Related edits advance freshness. Catalog ETags hash the response data and support conditional requests; a downgrade changes the ETag even without an organization edit. |
| Product editing destroyed IDs/prices and diverged from feeds | Existing product rows are updated rather than blindly replaced. Product rows are authoritative for publishing; a data migration preserves legacy descriptions. Tests assert ID, price and currency preservation and API review invalidation. The text editor still matches translated products by URL/name/order; a future ID-based editor would remove this ambiguity. |
| Login CSRF, weak account verification, persistent tokens | Session-establishing API login requires CSRF. New registrations and email changes require signed, expiring email confirmation. Disabled accounts cannot use confirmation to reactivate. Email uniqueness is case-insensitive at the database level. Credential changes revoke API tokens. Database-backed IP rate windows protect authentication endpoints. |
| Administrator access lacked MFA | Admin UI, staff dashboard sessions and staff API access require verified OTP sessions. Administrator bearer tokens are rejected. Tests exercise both denial and a successful authenticator sign-in. Existing administrators must enroll before production access. |
| Weak production defaults and vulnerable dependencies | WSGI/ASGI default to production. Production requires a strong secret, HTTPS canonical URL, email delivery configuration, and a webhook secret when Stripe is enabled. Runtime packages were updated and pinned; dependency audit reported no known vulnerabilities. CI runs tests, migration checks, dependency checks and advisory scanning. |
| Public/tracked invoice files and inconsistent invoice records | Uploaded invoices use private storage and authenticated downloads with `private, no-store`; type/size checks reject disguised uploads. Legacy invoice metadata is copied to canonical invoice records. Billing identity is snapshotted; sent status follows an explicit external send date. Tests cover private download, another customer's denial, snapshots, and invalid uploads. |
| Account closure removed financial records | Customers with financial history have credentials revoked and public companies removed while accounting records remain. Account closure first cancels the Stripe subscription and fails closed if cancellation fails. |
| Notification open redirects and stale reminders | Return destinations are restricted to the same host. Resolution is recorded separately from dismissal; conditions can resolve and later reopen. Canceled renewals no longer trigger renewal reminders. A schedulable command generates and resolves time-based notifications. |
| Large catalog responses consumed unbounded Python memory | JSON and NDJSON exports use chunked iteration; updates return 100 records per page with `next_page`. Tests parse the complete streamed JSON and check pagination. Streaming still uses database/server capacity: production proxy limits and caching are required for volumetric abuse. |

## Local validation and data changes

The final automated run passed 136 tests. The automated suite uses an isolated SQLite database, in-memory invoice storage, local email outbox, and mocked Stripe. It does not contact real customers or charge cards. Run:

```text
python manage.py test --settings=sentai.settings.test --noinput
python manage.py makemigrations --check --dry-run --settings=sentai.settings.test
python -m pip check
python -m pip_audit -r requirements.lock --disable-pip
```

The existing local SQLite database was backed up to `private_media/backups/before-security-20260906-182741.sqlite3` before migrations. Schema/data migrations were then applied locally. The private-invoice migration copied and byte-verified 24 files, including orphaned fixtures, before removing their public copies. The backup and private files are ignored by Git.

Invoice deletion appears in the working diff so the next commit stops shipping those files. Earlier Git commits still contain their contents. This change cannot retract previously downloaded or cached files, and it does not rewrite repository history.

The production configuration check passes its critical settings checks. Django reports two advisory warnings unless subdomain HSTS and preload are enabled. These remain explicit deployment choices because the app cannot establish that every subdomain supports HTTPS.

## Deployment steps still required

1. Back up the production database and media. Install `requirements.lock`. Use PostgreSQL for production row locking. Check for duplicate emails that differ only in case before migration; the new uniqueness constraint deliberately refuses duplicates rather than deleting accounts.
2. Set `DJANGO_SETTINGS_MODULE=sentai.settings.prod`, a strong `DJANGO_SECRET_KEY`, HTTPS `SITE_BASE_URL`, explicit hosts/CSRF origins, working SMTP settings, and matching Stripe keys/webhook secret. If TLS terminates at a proxy, enable `DJANGO_TRUST_PROXY` only when that proxy strips and replaces the forwarded-protocol header. Configure authentication rate limiting at the edge as well; the application limiter uses `REMOTE_ADDR`, not arbitrary forwarded headers.
3. Run `python manage.py migrate`, then `python manage.py migrate_private_invoices`. Private storage must be outside all served media locations. Block `/media/invoices/` in the reverse proxy/CDN before exposing the deployment and purge any cached invoice URLs. Back up private storage separately.
4. Enroll each administrator from the trusted server console: `python manage.py enroll_admin_mfa USERNAME`. The command privately prompts for the password and an authenticator code. Protect the enrollment URI as a secret. Existing administrators are not automatically enrolled. Keep a tested administrative recovery process under trusted server access.
5. Schedule `python manage.py maintain_billing --settings=sentai.settings.prod` every 15 minutes with the same environment as the web app. Capture errors and alert on nonzero exits. No scheduler was installed on a live server during this repair.
6. In Stripe test mode, run new checkout, duplicate submissions, successful and failed renewal, cancellation/reactivation, pending upgrade requiring authentication, failed upgrade, retried webhook, and return-page replay. Verify one intended invoice per upgrade and correct access before/after payment. Review historical standalone upgrade payments individually; the repair neither refunds nor recharges them automatically.
7. Send and confirm a real verification email, upload/download a test invoice as its owner and a different customer, and verify the proxy cannot serve the private or legacy invoice paths. Run deployment checks using the real production settings.

## Limits and follow-up scope

This is a code repair with automated regression coverage, not a guarantee that no undiscovered vulnerability remains. No live infrastructure, production PostgreSQL concurrency, SMTP delivery or Stripe end-to-end transaction was tested here.

The public API remains intentionally unauthenticated for crawlers. The CSP added here restricts objects, framing and base URLs, but does not yet enforce a strict script-source policy; replacing runtime CDN assets and moving inline scripts is a separate hardening task. Streaming exports should have reverse-proxy timeouts/rate limits. The update feed is a paginated view of currently public companies, not a durable deletion/tombstone log; consumers should periodically reconcile the full catalog.

Seller access to all prospects was explicitly documented as intended in the existing application and has been preserved. Changing that authorization policy requires a product decision. Splitting the large dashboard module, more admin lifecycle notifications, a clearer verification workflow, crawler discovery improvements and pricing changes remain the subsequent improvement phase.
