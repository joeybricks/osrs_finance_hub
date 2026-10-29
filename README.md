# OSRS Flip Finder

A local web app for finding Grand Exchange flips in Old School RuneScape. It
reads live prices from the [OSRS Wiki real-time prices API][api], works out the
margin after tax for every tradeable item, and ranks them by how much profit you
could realistically make in one four-hour buy-limit window.

Everything runs on your own machine. No account, no deployment, no database.

[api]: https://prices.runescape.wiki/

## What it does

**Flip scanner.** A sortable table of the best flips, ranked by estimated profit
per 4h. Filter by volume, price age, available capital, margin, ROI and
membership. Items whose live spread is far wider than their hourly average are
hidden by default, because that usually means a price spike or a manipulation
attempt rather than free money.

**Six views.** Home shows the best few from every other tab at a glance. The
Scanner ranks everything by profit over a four-hour window. Quick flips ranks
by profit per hour, for when you care how fast your gold comes back. Long term
finds items climbing over months that have pulled back from their peak. Sets
finds armour sets priced differently from their pieces. Magic finds items worth
alching or enchanting at your Magic level. Tabs across the top switch between
them, and the choice lives in the URL so you can bookmark one.

**Suggested prices.** Each flip comes with a concrete pair of offer prices to
place, chosen to still clear a profit after tax while filling sooner than the
passive prices would. See below.

**Live trades.** Star an item to keep it in a panel at the top of the page,
whatever your filters say. Record what you offered and how much has arrived,
list the stock back for sale in lots at whatever prices you choose, and it
tracks the profit on each lot, what is still unsold, and whether the rest of
your buy offer is still worth leaving up. Trades live in a file, so they
survive a restart.

**Safety rating.** A read of where an item's price has been going. A wide
margin on something that has been sliding for weeks is a trap, and this is what
warns you about it. See below, including why it runs on a button.

**Trade planner.** Inside any item, type a buy price, a sell price and a
quantity, and it shows the tax, the profit per unit and in total, the capital
needed, and a barometer rating how quickly the trade is likely to complete.

**Item search.** Type any item name, including ones that fail every flip filter.
Partial matches work, and so does a loose fuzzy match, so `abwhip` finds
`Abyssal whip`. Opening an item shows its full stats, alch values, examine text,
a link to its wiki page, and a price history chart over a selectable span.

## Install

Requires Python 3.10 or newer.

```bash
git clone <this repo>
cd ge_tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run

```bash
flipfinder serve --contact "you@example.com"
```

That starts the server on <http://127.0.0.1:8080/> and opens your browser. The
first load takes a few seconds while it fetches the item mapping and prices.

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--contact` | Contact string for the User-Agent. Please set this. |
| `--port` | Port to listen on. Default 8080. |
| `--host` | Bind address. Default 127.0.0.1, i.e. this machine only. |
| `--volume-fraction` | Share of traded volume you expect to capture. Default 0.10. |
| `--aggression` | Share of the post-tax margin conceded for a faster fill. Default 0.25. |
| `--min-profit-per-unit` | Floor a suggestion must clear after tax. Default 1. |
| `--latest-refresh` | Seconds between price refreshes. Default 60. |
| `--hourly-refresh` | Seconds between volume refreshes. Default 300. |
| `--positions-file` | Where pinned trades are kept. Default `~/.flipfinder/positions.json`. |
| `--no-browser` | Do not open a browser tab. |
| `--verbose` | Debug logging. |

## Configure

Every flag has an environment variable, which the flag overrides:

| Variable | Default | What it controls |
| --- | --- | --- |
| `FLIPFINDER_CONTACT` | unset | Your contact string in the User-Agent |
| `FLIPFINDER_PORT` | `8080` | Port |
| `FLIPFINDER_HOST` | `127.0.0.1` | Bind address |
| `FLIPFINDER_VOLUME_FRACTION` | `0.10` | Share of 4h volume you expect to capture |
| `FLIPFINDER_AGGRESSION` | `0.25` | Share of the post-tax margin conceded for a faster fill |
| `FLIPFINDER_MIN_PROFIT_PER_UNIT` | `1` | Floor a suggestion must clear after tax |
| `FLIPFINDER_TREND_TIMESTEP` | `24h` | History resolution behind the safety rating |
| `FLIPFINDER_TREND_BATCH_SIZE` | `25` | Most rows rated in one press of the button |
| `FLIPFINDER_POSITIONS_FILE` | `~/.flipfinder/positions.json` | Where pinned trades are kept |
| `FLIPFINDER_LATEST_REFRESH` | `60` | Seconds between `/latest` refreshes |
| `FLIPFINDER_HOURLY_REFRESH` | `300` | Seconds between `/1h` and `/5m` refreshes |
| `FLIPFINDER_TIMESERIES_CACHE` | `60` | Seconds to cache one item's chart data |
| `FLIPFINDER_OPEN_BROWSER` | `1` | Set to `0` to stop it opening a tab |

The defaults live in `flipfinder/config.py`.

### The contact string

The wiki asks every app that uses the prices API to identify itself with a way
to get in touch, so they can reach you rather than silently block you if
something goes wrong. The app sends:

```
osrs-flip-finder/0.1.0 (contact: you@example.com)
```

It runs without one and warns you on startup, but please set it. An email
address or a Discord handle is fine.

### The volume fraction

The single most important number to tune. The app assumes you can capture
`volume_fraction` of an item's four-hour traded volume. The default of 10% is
deliberately pessimistic, because you are competing with every other flipper
reading the same API. Raise it if you find you are filling orders faster than
the estimates suggest.

## How the maths works

Prices from this API have a confusing naming convention. `high` is the
**instant-buy** price, what you pay to buy right now. `low` is the
**instant-sell** price, what you receive to sell right now. A flipper buys near
`low` and sells near `high`.

For each item:

```
tax      = 2% of the sell price, rounded down,
           capped at 5,000,000 per item, and zero below 50gp
margin   = high - tax - low
roi      = margin / low
units    = min(buy limit, min(hourly buy vol, hourly sell vol) x 4 x volume_fraction)
profit   = margin x units
capital  = low x units

step         = spread x aggression / 2, reduced until the net clears the floor
suggest buy  = low + step
suggest sell = high - step
net per unit = suggest sell - tax(suggest sell) - suggest buy
```

Taking the thinner of the two hourly volumes matters: a flip needs both a seller
to buy from and a buyer to sell to, so the quieter side is the real constraint.

All of this lives in `flipfinder/flips.py`. The tax rules are three named
constants and one function, `ge_tax`, so if Jagex changes them you edit one
place.

### Suggested prices and how fast they fill

Buying at `low` and selling at `high` earns the widest margin, but those are the
slowest prices on the book. You join the back of a queue and wait for someone to
trade into you. Bidding a little above `low`, and asking a little below `high`,
jumps you ahead of that queue at the cost of margin.

The `aggression` setting is the share of your profit you are willing to give up
for a faster fill, split evenly across the two sides. The app then picks the most
aggressive price pair that still clears `min_profit_per_unit` after tax. If the
requested aggression would break that floor it backs off, so a suggestion may be
more passive than you asked for. An item that cannot clear the floor even at the
passive prices gets no suggestion at all.

It is measured against the post-tax margin, not the raw spread, because the
margin is what you actually lose. On expensive items the tax is most of the
spread. An Abyssal whip at 778,000 to 796,381 has a spread of 18,381 but a
margin of only 2,454, so conceding "a quarter of the spread" would cost 4,595 a
unit, nearly twice the entire profit. Budgeting against the margin means the
share you set is the share you give up, and the "Margin conceded" figure in the
item view always agrees with it.

Mithril dart tip, bought at 120 and sold at 140, works out like this at the
defaults:

| Quantity | Value |
| --- | --- |
| Raw spread | 20 gp |
| Tax on the sale | 2 gp |
| Passive margin | 18 gp |
| A quarter of that margin | 4 gp, so 2 a side |
| Suggested buy | 122 gp |
| Suggested sell | 138 gp |
| Net per unit | 14 gp |

You give up 4 gp a unit, a quarter of the margin, to be near the front of both
queues.

The net figure is guaranteed as arithmetic, not as an outcome. It is what you
make if both offers fill at those prices. Nothing in this app can promise that
they will, because the API publishes no order book depth, so there is no way to
know how many offers are already queued ahead of yours.

### Flip time

The scanner estimates hours to buy your units, hours to sell them, and the round
trip. Which volume drives which is the opposite of what the column names
suggest. Your buy offer is filled by players instant-selling, the second number
in the volume column. Your sell offer is filled by players instant-buying, the
first number.

There is a structural ceiling worth knowing about. Units are sized so that the
thinner side alone consumes the full four-hour window, so the round trip can
never exceed about eight hours at the default volume fraction. The filter
defaults to four hours, meaning the whole cycle completes inside one buy-limit
window. Raise it toward eight to see everything, or lower it for faster
turnover.

### Live trades

The scanner answers "what should I buy". Once you are in a trade the questions
change, and the filters that found the flip will often hide it again as the
spread moves. The Live trades panel at the top of the page is pinned open and
no filter touches it.

A real flip rarely happens in one go. You put an offer in for a few thousand,
they trickle in, you list what has arrived, more arrives, you list that too at
whatever price looks right by then. The panel is built around that.

Press the star on any row, or the button in its detail view, to pin it. Then
record three numbers, which you can edit at any time as things move:

| Field | Meaning |
| --- | --- |
| Bought at | The price on your buy offer |
| Offer size | How many you put the offer in for |
| Arrived so far | How many have actually filled |

From there the card shows a bar splitting your whole order four ways: sold,
listed for sale, held but not yet listed, and still out on the buy offer. Those
four always add up, so you can see at a glance where everything is.

#### Selling in lots

Each parcel you put up for sale is a lot, with its own quantity and asking
price. Add one with the quantity and price boxes at the bottom of the card; the
quantity defaults to whatever you are holding unlisted and the price to the
app's current suggestion. Each lot shows the profit per unit and in total, after
tax, against what that stock cost you, plus where its asking price sits against
the live book:

| Label | Meaning |
| --- | --- |
| Should go at once | At or under the best bid, so it crosses the spread |
| Near the front | At or under the best ask, so you are first in the queue |
| Above the market | Asking more than anyone else, so you are waiting |
| Sold | Marked sold, and counted as banked rather than hoped for |

Press **Sold** when a lot goes through. Its profit moves from "if all fills"
into "banked", and **Undo** puts it back if you press it by mistake. The totals
across the top of the panel separate the two for exactly this reason: banked is
money you have, projected is money you might get.

#### Should you keep buying?

This is the question the panel exists to answer. While units are still on your
buy offer, the card compares what you are paying against what the market will
pay you now, after tax, and says one of four things:

| Verdict | Meaning |
| --- | --- |
| Keep buying | Still worth a healthy amount per unit |
| Margin thinning | Still profitable, but well down on when you started |
| Stop buying | Every further unit breaks even or loses money |
| Offer filled | Nothing left to decide |

"Thinning" is measured against this trade rather than against nothing. The
first time you record a buy price, the app snapshots what the market was paying
at that moment, so it can later say the margin has fallen from one figure to
another instead of just guessing that things look worse.

Break-even is not simply what you paid. The tax comes off the sale, so covering
a buy of 1,000 gp needs a sale of 1,020, and the exemption and the cap bend that
at both ends. The app searches for the exact lowest whole price that clears,
rather than approximating, so the figure is never a gold piece out.

A pin with no buy price recorded is just a watch, which is useful for something
you want to keep an eye on before committing.

Break-even is not simply what you paid. The tax comes off the sale, so covering
a buy of 1,000 gp needs a sale of 1,020, and the exemption and the cap bend that
at both ends. The app searches for the exact lowest whole price that clears,
rather than approximating, so the figure is never a gold piece out.

#### Set trades

Press the star on a row in the Sets tab to pin that set as a trade, in the
direction the row shows. It joins the Live trades panel and counts towards its
totals like any other trade. To switch direction, unpin it and pin it again.

A set trade buys one thing and sells another, so the card has two halves.
**Buying** has a row for each item you buy, every piece when packing or the set
when unpacking, each with the same three numbers as a plain trade: bought at,
offer size, arrived so far. **Selling** has a block for each item you sell, with
its own lots, Sold buttons and outlook labels.

The exchange at the clerk is free and instant, so the card assumes you make it
as soon as you can. When packing, the piece you have least of decides how many
complete sets you have to sell. Pieces beyond that show as spare, waiting for
the rest to arrive. Banked, projected and "If all fills" only appear once every
item you are buying has a price recorded, because until then the cost of a set
is unknown.

Unpacking turns one purchase into several sales. To give each piece's lots a
profit of their own, the set's cost is shared across the pieces in proportion
to what each was selling for when you recorded the first buy price. The shares
always add up to exactly what the set cost, so the trade's total profit does
not depend on the split, only how it is attributed between pieces.

The keep-buying advice works per set: what one more set earns at your buy
prices and today's sell prices, compared with the same figure when you started.

#### Magic trades

Press the star on a row in the Magic tab to pin that spell on that item. It
joins Live trades and counts towards the totals like any other trade. The same
item can be pinned for two spells, say High Alchemy and an enchant.

The card has the usual bought at, offer size and arrived so far, plus two more:

- **Alched so far / Enchanted so far** – how many you have cast. This is what
  moves stock from bought to done.
- **Runes each** – what the runes for one item cost. It is priced from the
  market when you pin, using the Elemental runes setting at that moment, and
  you can change it to what you actually paid. It stays fixed after that, so
  a rune price moving does not rewrite your trade.

Each item costs its buy price plus its runes. Outlay counts runes only for
items you have cast.

**Alchemy** pays a fixed number of coins, so every item you cast is banked
profit straight away, and the rest you have bought are valued at the same
rate. There is nothing to sell, so there are no lots.

**Enchanting** has a Selling block for the enchanted item, with lots, Sold
buttons and outlook labels like a plain trade. Enchanted items not yet listed,
and items not yet cast, are valued at today's sell price for the projection.
The first buy price you record snapshots the enchanted item's instant-buy
price, and the keep-buying advice compares today's profit per item with that.

#### Where trades are stored

In `~/.flipfinder/positions.json`, not in the browser. This is your own record of
real gold, so clearing site data should not lose it. The file is small, written
whole on every change, and saved by writing a temporary file and renaming it, so
an interrupted write cannot corrupt it. A file that cannot be read is reported
and treated as empty rather than stopping the app.

Set trades and magic trades sit in the same file, beside the item trades. A
file from before they existed simply has none, and a set or magic trade the app
cannot read is skipped and reported without losing anything else.

Files written by an earlier version are upgraded on load. The old format had a
single quantity and one target price, and could not tell an order from what had
arrived, so the quantity becomes both and a target price becomes a lot for the
whole amount, which is what it meant in practice.

One deliberate nicety: prices refresh every minute and that redraws these cards.
If you are part way through typing a price when a refresh lands, the redraw is
held back until you leave the field, rather than throwing away what you typed.

### The safety rating

The flip maths only looks at this moment. An item can show a healthy margin
while its price has been falling for months, and buying into that is how you end
up holding stock worth less than you paid. The safety rating reads the price
history and says so.

One request at the daily timestep returns a full year of points. From those it
works out the gradient of the best-fit line through the prices, expressed as a
percentage of price per day, plus the change over 7 and 30 days, how far below
the window's peak the price sits, where it sits between the window's high and
low, and how much it swings day to day.

The rating comes from the gradient:

| Rating | Trend line |
| --- | --- |
| Safe | flat or rising |
| Caution | falling, under 0.2% a day |
| Risky | falling 0.2% to 0.5% a day |
| Dangerous | falling faster than 0.5% a day |

Two things drop it a band. A price that swings more than 8% a day is jumpy
enough that the margin can move while you hold. A price that is both falling and
sitting in the bottom 15% of its range is the classic falling knife, with nothing
underneath it. Coal, for example, currently rates Risky: down about 26% over the
year, 54% off its peak, and sitting near its low.

All the thresholds are named constants at the top of the trend section in
`flipfinder/flips.py`.

The planner uses the same figure. When a trade would take long enough that the
trend meaningfully eats the margin, over a fifth of it, it says so with the
numbers. For a fast flip on a liquid item the trend barely matters and it stays
quiet, which is correct: a 0.2% daily slide is nothing across 26 minutes.

#### Why it runs on a button

Trend data is the one thing with no bulk endpoint. Prices for all 4,663 items
come in a single request, but history has to be fetched one item at a time, so
rating the whole table would mean thousands of requests. That is exactly what
the wiki asks apps not to do.

So rating is deliberate rather than automatic. Pressing **Rate safety** fetches
history for the top 25 rows currently passing your filters, three requests at a
time, and caches every result for the session. Opening a single item rates just
that one. Long histories are cached for an hour, because a series of daily
points gains a new point once a day.

Items you have not rated show a dash, and the "hide rated risky or worse" filter
leaves them alone, because an unrated item is unknown rather than safe.

### Quick flips

The Scanner's ranking answers "what earns most in a four-hour window", which is
the right question when you are filling a buy limit. It is the wrong one when
you have more gold than patience, because it says nothing about how long your
capital sits tied up.

This view ranks by profit per hour instead, which is the four-hour profit
divided by the estimated round trip. An item earning 50,000 gp that cycles in
twenty minutes works your gold far harder than one earning the same over eight
hours.

One honest caveat, stated on the page itself. The buy limit still caps how much
you can buy in four hours, so a flip that cycles in one hour does not earn four
times as much. What it does is free your gold sooner, so you can put it into
something else.

Defaults are a round trip under an hour and at least 500 traded each side, since
a quick flip in a thin market is not quick at all.

### Long term

Rather than a spread to capture now, this looks for a position worth holding: a
price that has been climbing for months but is currently in a dip, so you are
buying a pullback in an uptrend instead of the top of a rise that already
happened. Items must still show a spread, so the position pays either way.

Two conditions have to hold at once:

| Condition | Default |
| --- | --- |
| Rising across the whole window | at least 0.05% a day |
| Currently off its peak | at least 5% below |

Both matter. Something falling is cheap for a reason, and something rising at
its all-time high has already made the move. The score multiplies the two, so a
steep climb well off its high sorts above a gentle climb near its top.

"Cheap" is deliberately measured as a pullback from the peak rather than a
position within the window's whole range. An item that has tripled over a year
and then given back a fifth is a dip worth buying, even though it still sits
high against where it started a year ago. Ranking by position in the range would
miss exactly the items this view is for.

#### Why it needs a button

Same constraint as the safety rating. Price history has no bulk endpoint, so
this cannot scan all 4,663 items. It shortlists candidates from the data already
in memory, by margin, volume and price age, orders them by profit, and sends the
top of that list to the server, which fetches history a few at a time and caches
everything. The batch is capped, adjustable on the page up to 50.

Anything already fetched is free, and it works both ways: a long-term scan fills
in the Safety column on the Scanner, and rating safety there makes a later scan
cheaper.

### Item sets

A Grand Exchange clerk swaps a set for its pieces, or the pieces for a set, for
free (right-click the clerk, Sets). So when the market prices a set differently
from its pieces added together, you can trade the gap either way:

| Direction | You buy | You sell |
| --- | --- | --- |
| Unpack | the set, at its `low` | each piece, at its `high` |
| Pack | each piece, at its `low` | the set, at its `high` |

```
unpack profit = sum(piece high - tax(piece high)) - set low
pack profit   = set high - tax(set high) - sum(piece low)
```

Each piece is a separate sale, so each pays its own tax with its own 5m cap.
That matters at the top end. Selling the three Inquisitor's pieces at around
400m between them pays about 8m in tax, 2% of each, while selling the set pays
only the 5m cap. That is why expensive sets often favour packing.

Sets per four hours follows the same rule as a single item, applied to every
item in the trade. It is your share of the thinnest hourly volume involved,
capped by the buy limit on what you are buying: the set's own limit when
unpacking, and the tightest piece's limit when packing. The Bottleneck column
names whichever of those binds. Buys of several pieces, or sales of several
pieces, are assumed to run side by side in separate GE slots, so each side
takes as long as its slowest item.

The **Instant** column is the profit per set if you crossed the spread on every
item right now, buying at `high` and selling at `low`. It is nearly always
negative. When it is positive the gap is wide enough to take without queueing.

Set prices go stale faster than most, because sets trade thinly. The age shown
is the stalest price of any item in the trade. Every set is listed by default,
with the ones that would not make money right now, or trade too thinly to fill
one in four hours, dimmed below the rest. The filters narrow it down by profit,
volume, capital and price age. A set is flagged as suspicious if any of its
items has a spiked spread. Click a row to see every item with the price, tax, volume
and age behind the figures.

Which sets exist and what they contain is not in the prices API. The list lives
in `flipfinder/set_data.py`, taken from the wiki's
[Item set](https://oldschool.runescape.wiki/w/Item_set) page in October 2026.
When Jagex adds a set, add a row there. A set whose items have vanished from the
mapping is skipped with a warning in the log rather than breaking the page.

### Magic: alchemy and enchanting

Buy an item, cast a spell on it, and either take the coins or sell what it
becomes:

| Method | Spell | What you get |
| --- | --- | --- |
| High Alchemy | 55 Magic, 1 nature + 5 fire | 60% of the item's value in coins |
| Low Alchemy | 21 Magic, 1 nature + 3 fire | 40% of the item's value in coins |
| Enchant jewellery | Lvl-1 to Lvl-7 Enchant, 7 to 93 Magic | the enchanted item, to sell |
| Enchant bolts | Enchant Crossbow Bolt, 4 to 87 Magic | ten enchanted bolts a cast, to sell |

```
alch profit    = alch value - item low - runes
enchant profit = output high - tax(output high) - item low - runes
```

Alch coins are not a GE sale, so they pay no tax. Runes are bought at their
`low`, like anything else bought patiently. A bolt spell's runes are shared
across the ten bolts it converts, rounded up so the cost is never understated.
**Instant** is the same sum bought and sold at the instant prices.

Elemental runes (air, water, earth, fire) count as free by default, because a
staff, tome or combination staff supplies them and that is how almost everyone
casts these spells. Set "Elemental runes" to "Bought on the GE" if you do not
have one. The other runes are always paid for.

Items per four hours is the smallest of three limits, and the Bottleneck
column names it: the item's buy limit, your share of its traded volume (and,
for enchanting, of the output's), and how many casts fit in four hours. High
Alchemy casts every 5 ticks, so 1,200 an hour; jewellery enchants every 3
ticks. Bolt spells can be cast every tick, which is why the GP and XP an hour
of casting for bolts look so high. For bolts the buy limit is what really
caps you.

Set your Magic level and everything above it is hidden. The level and the
staff choice are remembered on that device. Alchemy only lists items that pay
with a staff, because every item has an alch value and listing them all would
bury the few that pay. Every enchant is listed, with the ones that do not pay
right now dimmed.

Spell levels, runes, speeds, and which item enchants into which are not in
the prices API. They live in `flipfinder/magic_data.py`, taken from the wiki's
spell pages in October 2026. Enchants whose input or output cannot be traded
on the GE are left out.

### Home

The first tab. It shows the top five from Flips, Quick flips, Item sets and
Magic, each worked out with that tab's current filters, so the two never
disagree. Long term only appears once you have run its scan, because that
scan costs a request per item. Click a row to open the item, a set row to jump
to it on the Sets tab, or a heading for the full list.

### The price chart

The chart offers four spans: one week, one month, six months and one year.

Those are spans, not sampling intervals, and the difference matters. The API
always returns 365 points and you choose how far apart they are, so asking for
one point per day gives you a year of history rather than a day of it. Labelling
the control by interval made "24 hours" look like the shortest view available
when it was in fact the longest. The app now fetches the interval that covers
the span you asked for and trims the result to match.

One consequence worth knowing: six months and one year are drawn from the same
request, so switching between them costs nothing. Short spans show times on the
axis, long ones only dates.

### The trade planner and its barometer

The planner starts from the suggested prices and the estimated size, and
recalculates as you type. The money side is exact: tax, profit per unit, total
profit, total tax, capital needed and ROI all come from the same functions the
scanner uses, so the two can never disagree.

The barometer rates the round trip in four bands:

| Band | Round trip |
| --- | --- |
| Quick | under 1 hour |
| Moderate | 1 to 4 hours |
| Slow | 4 to 12 hours |
| Very slow | over 12 hours |

It starts from the volume estimate for a passive offer, then shortens it in
proportion to how far your price has crossed toward the other side of the
spread. Matching the best bid leaves the full wait. Meeting the best ask fills
at once. Sitting halfway roughly halves it.

Two cases get no rating at all. If either price sits outside the book on the
passive side, bidding under the best bid or asking above the best ask, the trade
only fills if the market moves to meet you, and volume cannot say whether it
will. If either side has no recent volume, there is nothing to estimate from.
Both show as Unknown, with a warning naming the reason.

The planner also warns when a trade loses money after tax, breaks exactly even,
exceeds the four-hour buy limit, rests on a suspiciously wide spread, or uses
prices more than ten minutes old. It never blocks you from planning a trade. It
tells you what it thinks and leaves the decision to you.

### The spread sanity check

An item whose live spread is more than three times its hourly average spread is
flagged as suspicious and hidden by default. This catches price spikes and
deliberate manipulation, where the book shows a huge margin that evaporates the
moment you try to trade it. Change the dropdown to flag or ignore instead, or
edit `DEFAULT_SPREAD_SPIKE_RATIO`.

### Keeping the browser honest

The page and its script are served with `Cache-Control: no-cache`, which means
the browser asks whether they have changed rather than guessing. Without it,
browsers cache heuristically and will happily run a stale script against a fresh
page, which produces the worst kind of bug: the markup is all there and nothing
responds to it. The ETag is still sent, so an unchanged file costs a short
"not modified" reply rather than a download.

## Being a good API citizen

The wiki blocks apps that misbehave, so the app is careful:

- It sends a descriptive User-Agent, never a library default.
- It only fetches the bulk endpoints. It never loops over items one request at
  a time. The one per-item endpoint, `/timeseries`, is called only when you open
  an item's detail view, and the result is cached.
- Everything is cached in memory and refreshed on a timer in the background.
  Loading the page, changing a filter and sorting a column all read from memory
  and never trigger a request.

At the default intervals that is 60 requests an hour for prices and 12 each for
the two volume windows: about 84 an hour, however long you leave it open and
however much you click around. Safety ratings and price charts add to that, but
only when you ask for them, and both are cached.

Responses are gzipped. The scanner payload is close to 4 MB of JSON and the page
refetches it every minute, which compresses to roughly a tenth of that. On a
desktop it hardly matters; over a phone connection it is the difference between
usable and not.

## When the API is down

A failed refresh keeps the last good data and records the error. The app shows
the stale numbers with a warning banner at the top rather than breaking. Price
age is in the last table column, so you can always see how old the numbers are.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The suite covers the tax cap and the sub-50gp exemption, margin and ROI, the
volume and buy-limit maths, the spread sanity check, ranking, the suggested
price search and its profit floor, the fill-time estimates, the planner's
totals, warnings and speed bands, the trend analysis and its safety bands,
break-even prices, set arbitrage in both directions with per-piece tax and
its volume and buy-limit bottlenecks, set trades with complete-set counting,
cost sharing across pieces and their own buy advice, the set trade routes,
alchemy and enchanting profit with and without a staff, rune costs shared
across bolts, the casting-speed cap, magic trades (alch coins banked as you
cast, enchanted items sold in lots, runes in the cost) and their routes, the
unit accounting across sell lots, the buy advice in all
four of its states, profit per hour, the long-term dip test against rising,
falling, flat and already-peaked prices, the trade store including a corrupt
file and the upgrade from the older format, and that the cache serves stale data
instead of crashing when the API fails.

## Layout

```
flipfinder/
  cli.py          command line entry point
  config.py       all tunable settings
  wiki_api.py     the API client, one method per endpoint
  cache.py        in-memory store and background refresh
  flips.py        the flip maths and trend analysis, pure functions, no I/O
  positions.py    live trades and their sell lots, saved to a small JSON file
  sets.py         item set arbitrage, pure functions over the per-item numbers
  set_data.py     which sets exist and what each contains, from the wiki
  magic.py        alchemy and enchanting profit, pure functions
  magic_data.py   spells, runes and enchant recipes, from the wiki
  server.py       FastAPI routes, including the planner and long-term endpoints
  static/         the single-page UI
tests/            unit tests
```

## Using it from a phone

The app binds to `127.0.0.1` by default, which means this machine only. To reach
it from a phone, start it with `--host` set to an address the phone can see.

On the same Wi-Fi, use the machine's LAN address. To reach it from anywhere,
install [Tailscale](https://tailscale.com) on both devices and bind to the
machine's Tailscale address:

```bash
flipfinder serve --contact you@example.com --host 100.x.y.z
```

Binding to the Tailscale address rather than `0.0.0.0` means only your own
devices can connect, not everyone on whatever network you have joined. The app
has no login and writes to your trades file, so that distinction matters. Start
Tailscale before the app, or the address will not exist yet and the bind fails.

Two things to expect. Type `http://` explicitly, because browsers that try
HTTPS first will fail against a plain HTTP server with a confusing "could not
connect" message. And the machine has to stay awake with the app running.

## Notes and caveats

- Estimates are estimates. The buy limit is per account per four hours, and the
  volume figures are the whole game's, not yours.
- The safety rating and the long-term view both describe the past. A year of
  decline or of growth is evidence, not a forecast, and an update can reverse
  any trend overnight.
- The app never logs in to anything and cannot place trades. It only reads
  public price data. Live trades are what you tell it you did, typed in by
  hand; it has no way to see your actual Grand Exchange offers, so marking a
  lot sold is something you do, not something it notices.
- Chart.js loads from a CDN, so the price chart needs an internet connection.
  The rest of the app works offline against cached data.
