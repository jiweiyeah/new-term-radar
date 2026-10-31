# Privacy

new-term-radar runs on your machine, or in your own GitHub Actions: the command line, the skills and the MCP server alike. Its author runs no server and collects nothing from it: no account, no telemetry, no analytics.

## What it reads, and where its requests go

Free, with no key:

- Hugging Face's trending models and Spaces (`huggingface.co`)
- GitHub's repository search and READMEs (`api.github.com`). A `GITHUB_TOKEN`, if you set one, goes only to GitHub and only raises the rate limit.
- the sitemaps listed in your `config.json` (the example config lists twelve AI tool, MCP and skill directories)
- the domains' own `ads.txt` files, the registry's RDAP answer, the Chrome Web Store's own pages and Disboard's own search: which ad publisher IDs a site shares with others, when a domain was registered, the low-star reviews of an extension, and how many Discord servers carry a name

Through the providers whose keys you set. A request carries what the radar is looking up or judging (X account names, terms and names, and the public posts and pages they came from) and nothing else from your machine:

- Monid (`api.monid.ai`), with `MONID_API_KEY`: X posts and searches, Reddit and YouTube reads, Google Ads volumes, DataForSEO Trends, domain data, the ratings of the sites on page one, the traffic of a new seller, the advertisers of a name, and (when the model gateway does not answer) the model calls
- the model gateway, with `OPENAI_API_KEY`: the model's calls, at the address in `OPENAI_BASE_URL` (by default `https://api.y-api.bestvirtualgoods.com/v1`): the terms and names being judged, the posts and pages they came from, and the instructions that ask for the answer
- AIsa (`tools.aisa.one`, `api.aisa.one`), with `AISA_API_KEY`, or the key the AIsa CLI stored at login: the DataForSEO readings (the searches, the Trends, the volumes, the difficulties), the door that answers when Monid's answer does not come back. The model calls never go there. The site ratings, a seller's traffic and the AdSense portfolios are Monid's alone
- TrustMRR (`trustmrr.com`), with `TRUSTMRR_API_KEY`: verified revenue listings
- Google Search Console (`googleapis.com`), with your own Google token: the queries your own site got
- Resend (`api.resend.com`), with `RESEND_API_KEY`: only to email the report, to the addresses you put in `RADAR_EMAIL_TO`
- Feishu (`open.feishu.cn`), with `FEISHU_WEBHOOK`: the report title and complete report, only to the group whose custom-bot webhook you configure

Each key goes only to its own provider. What a provider keeps of a request is governed by that provider's own privacy policy. Without a key, the signals that need it are skipped and the report says so; a lookup that cannot run without one stops with a message naming the key.

## What it keeps, on your machine only

In `RADAR_STATE` (by default `./state`; the skills keep theirs under `~/new-term-radar`; in Claude Code, the plugin's own data folder):

- `radar.db`: the leads it kept and their reviews 14 days later
- the snapshots that tell what is new since the last run: X vocabulary, GitHub stars, Hugging Face's top lists, sitemap URLs, new sites, sellers and revenue listings
- caches of keyword tables and site ratings, so a repeated lookup costs nothing

Reports go where you write them (`--out`). Delete the folder to remove all of it. Keys are never written there; a Search Console token is the file you put next to `config.json`, or `GSC_TOKEN_JSON`.

Notification settings may be stored in a local `.env` beside `config.json`, which is excluded from Git. The Feishu webhook acts as a credential and is not printed in notification logs.

## Contact

Questions or a problem: [open an issue](https://github.com/zhuyansen/new-term-radar/issues).
