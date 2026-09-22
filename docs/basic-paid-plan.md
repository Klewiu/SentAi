# Basic: administrator-managed annual pricing

100 PLN and 25 EUR are initial defaults, not minimum or mandatory prices. The application administrator can configure any positive annual Basic price, just as for other plans. The active database price takes precedence over environment defaults. Basic Checkout checks the remote Stripe amount against that database price, its currency and annual recurrence.

## Setup after cloning the repository

Git does not transfer the local database or `.env`. Prices configured on one developer's machine therefore do not automatically appear on another machine.

After migrations, use the application's administrator price management page to add a price for each plan/currency combination: Basic, Plus and Pro, each in PLN and EUR. Enter the amount in major units (e.g. `100`, not `10000`), the matching Stripe `price_...` ID, annual interval, and mark the price active for new customers. Use IDs from the Stripe account and test/live mode configured on that machine. Only one price per plan/currency can be active.

Changing a number in the app does not change the corresponding price in Stripe. When changing the charge, configure the matching Stripe price ID as well. Do not share customer databases or secret keys through Git. Missing prices indicate missing local configuration; they must not silently enable checkout at invented prices.

The `configure_basic_price` command below provisions the initial Basic defaults only. It is not a general price-editing or synchronization command and should not be rerun to replace customized prices.

Basic replaces the free offering. Its quotas remain one company, one language, all four formats. Stripe is an annual recurring subscription costing 100 PLN or 25 EUR according to the selected billing currency. Manual bank transfers are available exclusively for Pro, with the existing 14-day payment grace and 365-day access period. Plus and Pro prices are unchanged.

Publication now requires a current subscription or an eligible manual order. Unpaid/expired profiles remain stored and editable, but disappear from public profiles, feeds, sitemap and llms index. Former free customers are not charged automatically and must purchase a plan to publish. Existing administrator-assigned Plus/Pro accounts without billing history retain their prior entitlement.

Manual orders grant their recorded tier, not always Pro. Manual invoice and payment notifications identify Pro. Basic can upgrade to Plus or Pro on the existing Stripe subscription, using the existing full annual payment/no proration policy and pending updates. No new subscription is created for an upgrade.

## Stripe setup

Run `python manage.py configure_basic_price` with the intended environment credentials. It reuses stable lookup keys, validates the 10000 grosz PLN and 2500 cent EUR annual prices, and stores both BillingPlanPrice mappings. It does not charge or modify existing customer subscriptions. Alternatively configure `STRIPE_BASIC_PRICE_ID_PLN` and `STRIPE_BASIC_PRICE_ID_EUR` for matching annual prices. Checkout independently checks the selected remote Basic price before creating a session.

The local Stripe test account has been configured. Production credentials were not used. Repeat setup in production before offering live card purchases, apply migrations, and run a real hosted Checkout + webhook smoke test there.

Stripe reference: https://docs.stripe.com/api/prices/create

## Verification

Regression tests cover unpaid exclusion from all formats and indexes, paid Basic activation markers, cancellation/expiry, Pro manual grace expiry and paid restoration, invoice notifications, wrong recurrence rejection, PLN billing for EUR profiles, and pending upgrades without a second subscription.
