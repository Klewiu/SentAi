# SentAi

SentAi is a Django + DRF SaaS foundation for improving company visibility in AI search engines.

The platform lets clients or administrators publish structured company data as public endpoints that can be consumed by systems such as ChatGPT, Gemini, Perplexity, and other AI-driven search tools.

## What is included

- Django project with split settings for development and production.
- DRF API built with class-based views instead of viewsets.
- Login panel and lightweight customer dashboard.
- Custom user metadata with language preference and account type.
- Organization profiles with bilingual content fields for Polish and English.
- Plan system with `BASIC`, `PLUS`, and `PRO` tiers.
- Public AI-ready feeds:
  - `company.json`
  - `company.jsonld`
  - `llms.txt`
- Resource gating by plan for social profiles, tags, products, and content entries.
- Superuser/admin support through Django admin.

## Plan assumptions

The current scaffold uses the following product logic:

- `BASIC`
  - Free tier.
  - Core company profile.
  - Public `company.json`.
- `PLUS`
  - Adds `company.jsonld`.
  - Adds social profiles, tags, and content entries.
- `PRO`
  - Adds `llms.txt`.
  - Adds product catalog support.
  - Higher limits for all premium resources.

You can adjust these limits in `apps/subscriptions/models.py`.

## Quick start

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy environment variables from `.env.example` into `.env`.
  For Stripe test payments, set `STRIPE_SECRET_KEY` and optionally adjust `STRIPE_PLUS_PRICE_AMOUNT` and `STRIPE_PRO_PRICE_AMOUNT`.
4. Run migrations:

```bash
python manage.py migrate
```

5. Create a superuser:

```bash
python manage.py createsuperuser
```

6. Start the server:

```bash
python manage.py runserver
```

## Main URLs

- `/accounts/login/` - login form
- `/` - authenticated dashboard
- `/admin/` - superuser administration
- `/api/auth/login/` - token login endpoint
- `/api/auth/me/` - current user endpoint
- `/api/organizations/` - authenticated company management
- `/api/public/<slug>/company.json` - public basic feed
- `/api/public/<slug>/company.jsonld` - public JSON-LD feed for paid plans
- `/api/public/<slug>/llms.txt` - public LLM feed for PRO plans

## Architecture

- `apps/accounts` - user model and API authentication endpoints
- `apps/subscriptions` - plan tiers and subscription rules
- `apps/companies` - company profile data, premium resources, public publishing feeds
- `apps/dashboard` - HTML login/dashboard layer for operators and customers
- `sentai/settings` - environment-specific settings modules

## Notes

- The project is ready for PostgreSQL in production through environment variables.
- Stripe Checkout is wired for paid `PLUS` and `PRO` plans in test mode through environment variables.
- Public endpoints are open by design so AI crawlers can consume them directly. 

