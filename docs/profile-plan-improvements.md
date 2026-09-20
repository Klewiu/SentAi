# Company profiles: formats and plan content

Implemented locally on 6 September 2026. No price changes or billing migrations are required for this phase.

Every verified, public company that allows AI indexing now has JSON, JSON-LD, Markdown and llms.txt. The site-wide llms.txt and sitemap include eligible companies on all plans. Verification, publication and indexing consent still apply; purchasing a plan does not bypass them.

| Allowance | Free (BASIC internally) | Plus | Pro / Pro Manual |
| --- | --- | --- | --- |
| Company profiles | 1 | 2 | 3 |
| Languages per profile | 1 | 2 | 3 |
| Public file formats | All four | All four | All four |
| Company details and descriptions | Included | Included | Included |
| Specialties / keywords per profile | — | 25 | 100 |
| Social profiles per profile | — | 5 | 15 |
| Knowledge / FAQ entries per profile | — | 10 | 50 |
| Products / services per profile | — | — | 100 |

These content and company allowances preserve the existing limits. Social profile limits are ceilings; the current editor accepts the supported social networks. The form edits one featured FAQ, with additional entries available through the existing API.

The form now uses clearly labeled company details, optional public contact information, language selection, short and detailed descriptions, and plan-appropriate extra sections. It explains public email visibility, review requirements, and the difference between publishing readable information and a guaranteed AI citation. Product editing has individual name, description and link inputs; it no longer requires special pipe-separated text when JavaScript is available. A textarea fallback remains available without JavaScript.

Language selection defaults to the interface language. Free customers can switch their single language. Validation failures retain selected translations and entered text. Counts, required-field labels, an error summary, keyboard-compatible native controls, and an unsaved-changes warning help customers complete the form. Unavailable content sections are hidden. Saving a lower-tier profile preserves stored paid resources and archived translations, while public output continues to enforce the current tier.

Plan descriptions and the API guide now describe universal formats and content-based differentiation. Existing external format URLs remain unchanged.

Validation: 143 automated tests passed, including all formats on all tiers, hidden premium products, nonpublic-company denial, translation retention, checkbox submission, structured product data, and downgrade preservation. Isolated Chrome checks passed at 1280px and 390px for all plans, including switching the Free language and serializing a Pro product. Screenshots were inspected. Browser checks used generated local pages; server-side submission was covered separately by Django tests.

This implements the requested format availability and customer-form phase. Stable legal identity/domain verification, evidence workflows, a durable change feed with removals, multilingual discovery filters, a reference external-agent integration, and a measured visibility dashboard remain subsequent product work. No claims are made that crawlers have discovered or cited a company. The production rollout prerequisites in `security-repairs.md` still apply.
