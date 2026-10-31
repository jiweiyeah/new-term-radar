# Changelog

## 0.7.0 (2026-10-31)

### The paid readings go through Monid

The AIsa account's wallet ran empty on 2026-10-31: every data call and every chat call came
back refused ("insufficient wallet balance"). Monid resells the same DataForSEO data at
DataForSEO's price and carries the same X, Reddit and YouTube tools, so the paid readings now
go there first, and AIsa answers when Monid's answer does not come back. One key still runs
everything; two keys make Monid the one that pays. With both wallets empty the radar named the
refusal and went on with what it could read for free; that is the fallback now, not the normal
case: a keyword table for one name costs about $0.76 through Monid, and AIsa answers a Trends
task for about $0.011 — the same DataForSEO task, the same price.

- **One adapter for Google's own numbers** (`radar/dfs.py`): a search's first page, the Trends
  series and the related queries, the monthly searches of a keyword, its difficulty, a site's
  registration, the words a site ranks for, the pages that link to it. Monid is asked first,
  AIsa second, and the answer is the same shape either way.
- **X, Reddit, YouTube and the judge** (`radar/monid.py`): the posts and the searches now go
  through Monid's `anyapi` tools, and the name judge through Monid's OpenAI-compatible chat
  proxy (`gpt-4.1-mini`, `gpt-4o-mini`, `gemini-2.5-flash`, `deepseek-chat`, `claude-sonnet-4-6`;
  `qwen-flash` was AIsa's and Monid does not carry it, so `config.json` names `gpt-4.1-mini`).
- **An empty wallet no longer takes the key down with it** (`radar/aisa.py`): the wallet belongs
  to the account, not to the key. A refusal for an empty wallet is named ("the AIsa wallet is
  empty") and the key stays alive, so a top-up makes it work again without a manual reset.
  Before, the key was marked dead for that and every later run skipped it.
- **Cheaper where it counts, dearer where it does not**: the Labs calls are $0.012 a call plus
  $0.00012 a keyword (AIsa charged $0.09 a call for the volume and $0.012 for the difficulty),
  the ChatGPT answers about $0.0007 each (was about $0.004), the X posts and searches about
  $0.002 a call (the list price is $0.0001 a unit), and the domains a site's backlinks come from
  $0.024 a call plus $0.000036 a row. The Trends calls are the exception: Monid charges $0.011
  a call, and AIsa charges the same $0.011 for the same task when it is measured (2026-10-31; the
  $0.0012 in the earlier notes does not hold for it), and the keyword table asks for two a row, plus the country
  series and the word's age. The table that cost about $0.14 for one name now costs about $0.70,
  and about $1.05 for five (AIsa: about $0.34); with the example config a month is about $38
  instead of about $5.

### The model's calls go through an OpenAI-compatible gateway

The model calls — the name judge, the keyword table's phrases, the pains, the sellers' kinds — do not
go through AIsa any more. They go to an OpenAI-compatible gateway: `OPENAI_BASE_URL` with
`OPENAI_API_KEY`. The `openai` SDK is used when it is installed, the standard library's urllib when it
is not — the same protocol, so the package still installs on the standard library alone
(`radar/llm.py`).

- **AIsa is not a model door any more** (`radar/aisa.py`): its chat endpoint is gone, and with it the
  second wallet the key ledger kept apart (one scope now, `data`). AIsa answers Google's own numbers
  and only those. Monid's chat is the second door for the model ($0.002 a call), and both failures are
  named.
- **The gateway names its models with the maker in front** ("openai/gpt-5.4-mini"), so the name in
  `config.json` is looked up in the gateway's own model list, and a model it does not carry falls back
  to a short chain of cheap ones. The model that answered is named in the report (`model` in the JSON).
- **The config's models now name what the gateway carries**: `gpt-5.4-mini` for the judge, the pains,
  the asks and the sellers, `claude-sonnet-5` for the use cases. A name the gateway does not know is
  still tried first, so an old config keeps working.
- The two model clients are told apart in the tests: the SDK path and the standard-library path both
  speak the same protocol, and only a connection that never left is asked again (`radar/llm.py`).

### GoAnyAPI is gone

The four readings GoAnyAPI gave the radar now come from Monid (https://monid.ai/tools) and from the sources
themselves. The key is `MONID_API_KEY` now; `GOANYAPI_API_KEY` is no longer read.

- **The rating of each site on page one** — Ahrefs through Monid, about $0.0013 a site, kept 30 days (was $0.002
  through GoAnyAPI). `radar/monid.py` runs the call and waits for its answer: a run on Monid is asynchronous, so
  every call polls until the run is done.
- **The traffic of a new seller** — Similarweb through Monid, about $0.0017 a site: the monthly visits, where they
  come from and the share that comes from AI answers. The domain's registration date no longer comes inside that
  answer: it is the registry's own RDAP answer now (`radar/rdap.py`), free, and it works for every suffix.
- **The other domains on a paying site's AdSense account** — the site's own `ads.txt` says its publisher ID,
  Google's Ads Transparency Center (through Monid, about $0.0048 a site: it is asked under the site's own name and
  under its own domain, so the brand's family and the near-misses on it are both in) says which domains run ads
  naming the site, and each of those domains' own `ads.txt` says whether the publisher ID is the same one. A domain
  that only mentions the name in its ads shares no ID and is dropped, and a site carrying several publisher IDs is
  read through the one with the most sites behind it. `radar/adsense.py`.
  Google keeps no list of the domains on a publisher ID, so a media group's whole portfolio can no longer be
  counted the way it was: the bar for "a network, not a person" is now six of the domains that name the site on
  one ID (`max_sisters`, 5).
- **The Chrome Web Store's low-star reviews** — the store's own call, read the way the store's page reads it: the
  page holds no review text, it says which internal call to make, so one GET says how to ask (the id of the reviews
  call, the session id, the build id) and one POST per extension and star level brings the reviews back. Free, no
  key, and the id is read off the page because it rotates (`radar/cws.py`). It was 3 credits a page.

The docs said GoAnyAPI also answered the related queries and the one-country Trends series. Both come from
DataForSEO through AIsa now (`radar/google.py`), like the rest of Google's own numbers, and the docs say so.

### Fixed on the way

- **The model that judges a name could take the whole table with it** (`radar/usecases.py`): the call that
  proposes the keywords was made with no guard (the screen below it had one), so a refused model call ended
  the keyword table with a traceback: the day's names, their terms and their sizes, all gone. The name now
  goes on with the related queries alone, and the failure is named under the table.
- **A week of paid-for Reddit posts could be lost to one refused lookup** (`radar/asks.py`): the call for the
  searches of a keyword was not guarded. It is now: the posts are kept, the jobs are listed without their
  volume, and the error is named.
- **An answer in an unexpected shape could take the table down** (`radar/usecases.py`, `radar/dfs.py`): a row
  without a keyword, a month without a year, a Trends point with a broken date. Such a row is left out now,
  not raised.
- **The second door's Trends answers were not read** (`radar/google.py`, `radar/markets.py`): the item type was
  matched exactly ("google_trends_graph"), and AIsa answers "dataforseo_trends_graph": the country series, the
  related queries and the top region came back empty without a word. The match is loose now, the way the
  review's already was.
- **An empty wallet spelled differently marked the key dead** (`radar/aisa.py`): the wallet words were matched
  with their case, so "Insufficient Wallet Balance" did not match, and a batch where only some of the calls
  failed marked the key dead and bought the whole batch again on the next key. Both are fixed: the words are
  matched in lower case, and a key that worked for some calls of a batch is not a refused key.
- **The same difficulty was bought twice a run** (`radar/usecases.py`, `radar/competition.py`): the markets
  reading buys the difficulty for the table's own market, and the page-one check bought it again. The second
  reading reuses the first: $0.012 a run back.
- **A missing rating blamed a key that was there** (`radar/competition.py`, `radar/report.py`): when no site on
  page one came back with a rating, the report said "No MONID_API_KEY" — but a key that is present and a wallet
  that is empty look the same from there, and the truth was the second: the ratings were refused for an empty
  wallet and the report said nothing about it. The ratings now name what went wrong ("ratings: Not enough wallet
  balance: ..."), one line per distinct reason, and the report says that the ratings did not come back instead of
  blaming a key that is there.
- **A run whose answer never came was bought a second time** (`radar/monid.py`, `radar/dfs.py`): a Monid run
  that was created and never answered may already have been billed, so the second door no longer buys the same
  reading again. A run is also given up on by a real deadline now (180 s), not by ninety polls of sixty.
- **The vendor's answer is no longer echoed into the report** (`radar/monid.py`): the whole answer could travel
  into the report and out by mail, request and all. Only the reason is kept now.
- **A line that broke before the request was delivered is asked again** (`radar/net.py`): about one call
  in six to api.monid.ai ends the TLS handshake early ("UNEXPECTED_EOF_WHILE_READING"), and the reading was
  lost: the run had asked for it, and the table came out with a hole in it. A handshake that broke sends
  nothing, so the call is asked again (three times, with a pause). A request that was delivered and whose
  answer was cut is not asked again: it may already have been billed. Every HTTP call in the radar goes
  through it now, the paid readings and the free ones alike.
- **A search that failed was read as an empty search** (`radar/google.py`): a DataForSEO task that its own server
  failed on (40101, seen on about a quarter of the calls) came back as no results at all, and the radar carried on
  as if the page had nothing on it. The search is asked again now, on the other search (the live one and the
  cached one fail apart), and a failure that is not a server's fault is raised, so the report names it.
- **The pains signal's two searches no longer use Google's `site:` operator** (`radar/pains.py`): DataForSEO answers
  it with the real address hidden behind Google's own redirect (and sometimes with nothing from the site at all),
  so the Chrome Web Store and YouTube are asked for by name and picked out of the answer. The extensions a keyword
  brings are found again. The extensions themselves now come from the store's own search (one GET of
  `chromewebstore.google.com/search/<keyword>`, free): it knows which extensions carry the keyword, and the
  search engine's index of the store is thin and slow to answer.
- **The Discord servers beside a name are counted from Disboard's own search** (`radar/sidelights.py`): the count
  came from a `site:disboard.org` search, which DataForSEO answers with the address hidden behind Google's own
  redirect, so the count was always zero. Disboard's search is a form: its page is asked for the token the form
  needs, the name is posted to it, and the servers in the answer are counted. Free.
- **A site's name is its name** (`radar/domains.py`): "vogue.com.tw" was called "com" and "ai-tools.co.uk" its
  whole address. The name is now the part before the suffix, and "www." and the usual subdomains are left out.
- **A model that is not paid for no longer takes the key down with it** (`radar/aisa.py`): AIsa refuses a model
  that needs a top-up with the same 402 as an empty wallet, and the radar marked the whole key dead for chat calls
  on that, so every model-driven signal failed after one such call. The key now stays alive, and the refusal is
  reported as it is: "model gpt-4.1-mini requires a paid balance".
- **A reading that cannot be bought no longer takes the table down with it** (`radar/usecases.py`): the search
  volumes and the Trends curves were read outside any guard, so an empty wallet (or a refused call) ended the
  keyword table with a traceback instead of a table. The failure is named under the table now ("Some readings
  failed, so the table holds less: ..."), the rows read "no data" and nothing is prioritized on a number the radar
  does not have.
- **The related queries and the one-country series need a key to be bought** (`radar/usecases.py`, `radar/dfs.py`):
  they were off when there was no key (they were GoAnyAPI's, and GoAnyAPI had its own key). Now they follow the
  same rule as the other paid readings: with no Monid key and no AIsa key they are not tried at all, and the table
  says so instead of listing a failure for every name.

## 0.6.1 (2026-10-28)

- Nothing goes out without the user's yes. The three skills and the MCP server's instructions now say the radar
  only reads: before an agent posts, publishes, submits, emails or buys anything for the user on the strength of a
  result, it shows where it goes and the exact text or the cost, and waits for the user's yes to that one. A row
  marked `prioritize` is not that yes, and one yes covers one action. Nothing else changed.

## 0.6.0 (2026-10-27)

What was built after 0.5.0 on a separate line (it ran there as 0.5.3, 0.6.0 and 0.7.0), now published together with
0.5.1 and 0.5.2, and three changes to the new-sites signal and the page-one check.

### Fixed

- **③ New sites ran again**: AIsa's tool router checks arguments against a schema that types `filters` as objects, so
  the whois overview's DataForSEO filters have been refused there since about 2026-10-15 and the weekly signal failed.
  It goes through AIsa's REST passthrough now (`aisa.rest()`), which takes them as DataForSEO does. A query that fails
  is named; all of them failing raises.

### New sites and page one

- **The first words a new site ranks for** (`radar/site_words.py`, `words` in `new_sites`): each site new this run
  comes with its five words in Google's top 20, the most traffic first, in any country (DataForSEO Labs' ranked
  keywords, about $0.013 a site). On 2026-10-27 one site registered in August ranked 5 to 11 in Taiwan and for nothing
  in the US top 20. A word new since Labs' previous check shows the two checks it fell between.
- **How old page one's sites are, recorded only** (`radar/page_age.py`, `page_age` in `competition`): each page-one
  result's domain dated by its registration (RDAP, free, one request every half second, kept 30 days), and the results
  that entered the top 10 since the reading before. In report.json as each row's `page_age`; shown nowhere and used in
  no decision until later rankings show it says something.

### The keyword table: twelve months, your rank, threads, the .com, AI answers

Four free readings on the keyword table and a paid, opt-in one on ⑦ asks. Each has its own config block with `"enabled"`.

- **12 months** (`radar/curve.py`, `year_curve`): the volume call (`usecases.volumes`) now keeps each phrase's monthly searches, oldest first (`months`), and the table has a `12 months` column after `Now`: bars on the term's own scale from zero and a word, `new since` (barely searched a year ago), `peaked` (a burst past its top: twice the median month or more, at half of it or less now), `falling`, `rising`, `steady`, or `few searches` under 50 in its best month. Every row with Google Ads data; free.
- **Your rank** (`radar/yours.py`, `yours`): your sites (`yours.domains`, else the domain of `gsc.site`) are looked for in the page-one check's ten results and in Search Console (`gsc.positions()`: average position, impressions and clicks for the exact queries over `days`, one call, an RE2 filter). The decision cell ends with the action: `→ update yours` (page one by either), `→ improve yours` (Search Console average 11 to 30), `→ new page`; your pages are listed under the table. Prioritize and watch rows; free.
- **What else holds page one** (`radar/competition.py`): the page-one check keeps the forum threads on page one (`forums`: forum results and the "Discussions and forums" block), and the page-one cell marks an AI overview, an answer box, forums, videos and news. Under the table: a note on the clicks an AI overview takes (Pew Research Center, 2025) and the threads, best-of searches first. Free.
- **Exact-match .com** (`radar/dotcom.py`, `dot_com`): RDAP (Verisign) for the joined and hyphenated .com of the prioritized rows, names first, up to `max_rows`; registered with its date, or not registered. Free.
- **AI answers** (`radar/ai_answers.py`, `ai_answers`, off by default): ⑦'s jobs with searches are asked of ChatGPT (DataForSEO's LLM scraper: its interface's answer with search) and, optionally, Google's AI Mode, as "What is the best tool to {job}?"; the asks table gains an `AI answers name` column, and under it the sites each answer cites, ✅ when one is yours. About $0.004 an answer.
- `gsc._query()` takes dimension filters and returns positions.
- Tests: the RDAP lookup is blocked in tests like the paid calls; `dot_com` is off unless a test turns it on.

Measured on 2026-10-20: five "claude skills" terms through the new keyword readings, $0.11 (the volume call and the page-one check; the four readings themselves cost nothing); six AI answers, $0.024. Found on the way and fixed before this entry: AI Mode's reference names included "Reddit", so its tool names are now the bold names that open its list items; and a Search Console average of 89 with one impression no longer counts as a page to improve.

### The keyword table: word age, markets, page-one shape

Three readings added to the keyword table, for the rows that would be acted on. Each has its own config block and `"enabled": false` turns it off.

- **Word age** (`radar/wordage.py`, `word_age`): the first week with any interest in the last five years (DataForSEO Trends, `past_5_years`) is the column `First searched`; 🆕 = within `new_days` (180). The week still running and Trends' unsettled last two days are left out (`review.settled()`), so a "0, 100" tail cannot make a term look new. A new term's page one is read one step easier (crowded → mixed, mixed → open), so a rising new term is no longer demoted to watch for a crowded page one it has not had time to shape. Prioritize and watch rows; $0.0012 a row.
- **Markets** (`radar/markets.py`, `markets`): Google Ads monthly searches in each market of `codes` (US, DE, FR, JP by default, all languages), the keyword difficulty there (`difficulty`), `best` market (most searches per point of difficulty, or most searches when a difficulty is missing) and the Trends top region among the major markets (`top_region`). Prioritize and watch rows; about $0.41 a run with the defaults (four $0.09 volume calls, four difficulty calls at about $0.012) plus $0.0024 a row.
- **Page-one shape** (`radar/serpshape.py`, `serp_shape`): from the live top ten (US / en / desktop by default), homepages against inner pages, results on high-authority platforms (configurable), titles that hold the term, an exact-match homepage, and #1 and #2. Verdict `platform-held` (half or more on platforms), `beatable` (mostly inner pages, no exact-match homepage) or `contested`. Prioritize rows; reuses the page-one check's results, otherwise about $0.002 a term.
- The keyword table has three new columns (First searched, Markets, Page-one shape) in English and Chinese, a note on what they mean, and the #1 and #2 of the prioritized rows under it. Failures of these readings are listed under the table and leave the rest of it as it was.
- `competition.page_one()` takes a language and a device; `competition.difficulty()` takes a language; the page-one check keeps its ten results on the row (`competition.results`).

Measured on 2026-10-18 for two terms: $0.42 for the three readings.

### More signals

- **⑧ sellers**: Paddle Classic, Dodo Payments and Waffo Pancake beside Stripe, Polar, Lemon Squeezy, Gumroad and Creem;
  each new seller's domain registration and where its visitors come from, with the AI share (GoAnyAPI); a payment link
  on several new sites is one network's, not new sellers (1,465 keyword domains on one template shared a few links in
  September and October 2026); a builder's or host's default words (Notion, Framer, ReadMe, Gitea, a link shortener)
  are not the seller's own, and forums are not sellers.
- **⑧b builders**: sites just built with Lovable or v0 and newsletters just started on beehiiv, read off their badges;
  only the software among them becomes a lead.
- **⑨ just listed** (`radar/feeds.py`): Product Hunt, Show HN, OpenRouter, Civitai, the App Store's grossing charts,
  awesome lists, Google's new pages under Figma, There's An AI For That and the Chrome Web Store, trending searches, and
  rising searches next to root words (the paid ones weekly). A first run lists what each source itself dates as recent.
- **⑩ money** (`radar/money.py`, weekly, GoAnyAPI): the other sites on a paying tool site's AdSense account, and the
  advertisers new on your own terms.
- **Beside the names** (`radar/sidelights.py`): new sites carrying a name (crt.sh certificates, whois), its Wikipedia
  views, what Google knows it as (a name taken by something else is said so), and Discord servers; the searches around
  the paying products.
- **⑦ asks** also read X, Software Recommendations and Freelancer.com, drop old posts and name each post's source; the
  tool sites on page one for the day's terms go to ⑩, big sites and ad networks left out.
- **history**: `history update` adds the sellers that appeared since the history's newest day (weekly, about $0.5);
  `history sort --rules` sorts by keywords alone, free, and a later sort with the model redoes those.
- The status says how long each signal took when one took a minute or more.

## 0.5.2 (2026-10-26)

- The MCP server's failures reach the agent with their reason (no key, a refused call, a state folder it cannot write).

## 0.5.1 (2026-10-24)

- The skills run the pinned release from PyPI, ask before any paid call, and read fetched text as data; with no
  `config.json` the radar runs on the example it ships with. "DataForSEO Trends" where the radar reads it.

## 0.5.0 (2026-10-20)

- ⑧ sellers: the sites that just started taking money, new by the day each first linked to the platform; `history`.

## Earlier

See the git history.
