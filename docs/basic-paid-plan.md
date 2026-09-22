# Basic: 100 PLN or 25 EUR per year

Basic replaces the free offering. Its quotas remain one company, one language, all four formats. Stripe is an annual recurring subscription costing 100 PLN or 25 EUR according to the selected billing currency. Manual bank transfers are available exclusively for Pro, with the existing 14-day payment grace and 365-day access period. Plus and Pro prices are unchanged.

Publication now requires a current subscription or an eligible manual order. Unpaid/expired profiles remain stored and editable, but disappear from public profiles, feeds, sitemap and llms index. Former free customers are not charged automatically and must purchase a plan to publish. Existing administrator-assigned Plus/Pro accounts without billing history retain their prior entitlement.

Manual orders grant their recorded tier, not always Pro. Manual invoice and payment notifications identify Pro. Basic can upgrade to Plus or Pro on the existing Stripe subscription, using the existing full annual payment/no proration policy and pending updates. No new subscription is created for an upgrade.

## Stripe setup

Run `python manage.py configure_basic_price` with the intended environment credentials. It reuses stable lookup keys, validates the 10000 grosz PLN and 2500 cent EUR annual prices, and stores both BillingPlanPrice mappings. It does not charge or modify existing customer subscriptions. Alternatively configure `STRIPE_BASIC_PRICE_ID_PLN` and `STRIPE_BASIC_PRICE_ID_EUR` for matching annual prices. Checkout independently checks the selected remote Basic price before creating a session.

The local Stripe test account has been configured. Production credentials were not used. Repeat setup in production before offering live card purchases, apply migrations, and run a real hosted Checkout + webhook smoke test there.

Stripe reference: https://docs.stripe.com/api/prices/create

## Verification

Regression tests cover unpaid exclusion from all formats and indexes, paid Basic activation markers, cancellation/expiry, Pro manual grace expiry and paid restoration, invoice notifications, wrong recurrence rejection, PLN billing for EUR profiles, and pending upgrades without a second subscription.
